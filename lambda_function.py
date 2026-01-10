import boto3
import os
import time
import urllib.parse
import re
from datetime import datetime

s3 = boto3.client('s3')
textract = boto3.client('textract')
dynamodb = boto3.resource('dynamodb')

TABLE_NAME = os.environ['DYNAMODB_TABLE']

def parse_date(date_text):
    if not date_text: return None
    formats = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y"]
    clean_text = re.split(r'\s+|/', date_text)[0]
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', date_text)
    if match: clean_text = match.group(1)
    for fmt in formats:
        try: return str(int(datetime.strptime(clean_text, fmt).timestamp()))
        except ValueError: continue
    return None

def clean_value(val):
    if not val: return None
    
    if val.upper() in ["FRE", "KS", "EZ", "Z3E", "MDF", "SEE NOTE", "PAGE", "OF"]: return None
    match = re.search(r'([<>]?)\s*(\d+(\.\d+)?)', val)
    if match: return match.group(2)
    return None

def process_pdf(bucket, key, user_id, file_key, upload_timestamp, table):
    print(f"📄 Starting FULL analysis for {key}...")
    
    start_response = textract.start_document_analysis(
        DocumentLocation={'S3Object': {'Bucket': bucket, 'Name': key}},
        FeatureTypes=['TABLES', 'QUERIES'],
        QueriesConfig={'Queries': [{'Text': "Date Collected?", 'Alias': "DATE_COLLECTED"}]}
    )
    job_id = start_response['JobId']
    
    
    status = "IN_PROGRESS"
    while status == "IN_PROGRESS":
        time.sleep(2)
        response = textract.get_document_analysis(JobId=job_id)
        status = response['JobStatus']
        
    if status != "SUCCEEDED":
        print(f"❌ Textract Failed: {status}")
        return

    
    all_blocks = []
    next_token = None
    
    while True:
        params = {'JobId': job_id}
        if next_token:
            params['NextToken'] = next_token
            
        response = textract.get_document_analysis(**params)
        all_blocks.extend(response['Blocks'])
        
        next_token = response.get('NextToken')
        if not next_token:
            break
            
    print(f"   -> Retrieved {len(all_blocks)} blocks of data.")

    
    collection_date = upload_timestamp 
    text_map = {b['Id']: b['Text'] for b in all_blocks if 'Text' in b}
    
    for block in all_blocks:
        if block['BlockType'] == 'QUERY' and block['Query']['Alias'] == 'DATE_COLLECTED':
            for rel in block.get('Relationships', []):
                if rel['Type'] == 'ANSWER':
                    ans_id = rel['Ids'][0]
                    raw_date = text_map.get(ans_id, "")
                    parsed = parse_date(raw_date)
                    if parsed: collection_date = parsed

    
    with table.batch_writer() as batch:
        for block in all_blocks:
            if block['BlockType'] == 'TABLE':
                cell_map = {} 
                if 'Relationships' in block:
                    for rel in block['Relationships']:
                        if rel['Type'] == 'CHILD':
                            for child_id in rel['Ids']:
                                cell = next((b for b in all_blocks if b['Id'] == child_id), None)
                                if cell and cell['BlockType'] == 'CELL':
                                    row = cell['RowIndex']
                                    col = cell['ColumnIndex']
                                    txt = ""
                                    if 'Relationships' in cell:
                                        for cr in cell['Relationships']:
                                            if cr['Type'] == 'CHILD':
                                                for wid in cr['Ids']:
                                                    txt += text_map.get(wid, "") + " "
                                    cell_map[(row, col)] = txt.strip()
                
                
                max_row = max([r for r, c in cell_map.keys()] or [0])
                for r in range(1, max_row + 1):
                    test_name = cell_map.get((r, 1), "")
                    
                    if len(test_name) < 2 or len(test_name) > 80: continue
                    bad_words = ["Test Name", "Reference Range", "DOB:", "Patient", "Gender", "Collected:", "Received:", "Reported:"]
                    if any(w in test_name for w in bad_words): continue

                    raw_val = clean_value(cell_map.get((r, 3), ""))
                    if not raw_val:
                        raw_val = clean_value(cell_map.get((r, 2), ""))
                        
                    if raw_val:
                        print(f"   -> Found {test_name}: {raw_val}")
                        item = {
                            'user_id': user_id,
                            'record_id': f"{test_name}_{file_key}",
                            'metric': test_name,
                            'value': raw_val,
                            'unit': 'extracted',
                            'source_file': file_key,
                            'upload_timestamp': collection_date
                        }
                        batch.put_item(Item=item)

def lambda_handler(event, context):
    try:
        record = event['Records'][0]
        bucket_name = record['s3']['bucket']['name']
        file_key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        
        if file_key.endswith('.pdf'):
            table = dynamodb.Table(TABLE_NAME)
            parts = file_key.split('/')
            user_id = parts[1] if len(parts) > 1 else "unknown"
            process_pdf(bucket_name, file_key, user_id, file_key, str(int(time.time())), table)
            
        return {"statusCode": 200, "body": "Success"}
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise e