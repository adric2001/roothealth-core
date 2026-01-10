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

def normalize_metric_name(name):
    if not name: return ""
    name = name.upper().strip()
    name = re.sub(r'\s+', ' ', name)
    name = name.replace(',', ', ')
    name = name.replace(' ,', ',')
    name = name.replace(',  ', ', ')
    
    if "TESTOSTERONE" in name and "TOTAL" in name:
        if "FREE" not in name or "MS TESTOSTERONE" in name: 
             return "TESTOSTERONE, TOTAL"
             
    if "ESTRADIOL" in name and "ULTRASENSITIVE" in name:
        return "ESTRADIOL, ULTRASENSITIVE"

    return name.title()

def process_pdf(bucket, key, user_id, file_key, upload_timestamp, table):
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
        return

    all_blocks = []
    next_token = None
    while True:
        params = {'JobId': job_id}
        if next_token: params['NextToken'] = next_token
        response = textract.get_document_analysis(**params)
        all_blocks.extend(response['Blocks'])
        next_token = response.get('NextToken')
        if not next_token: break
            
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
                    raw_name = cell_map.get((r, 1), "")
                    
                    if len(raw_name) < 3: continue
                    if re.match(r'^[\d\.\-]+$', raw_name.replace(' ', '')): continue
                    
                    bad_phrases = [
                        "HIGHER RELATIVE", "CONSIDER EXCLUDE", "FOR ADDITIONAL INFORMATION", 
                        "PURPOSES ONLY", "Z SCORE", "REFERENCE RANGE", "PAGE OF", 
                        "LAB REF", "COLLECTED", "RECEIVED", "REPORTED", "SEE NOTE",
                        "PLEASE REFER TO", "INTERPRETATION"
                    ]
                    if any(phrase in raw_name.upper() for phrase in bad_phrases): continue
                    if len(raw_name) > 60: continue

                    test_name = normalize_metric_name(raw_name)

                    raw_val = clean_value(cell_map.get((r, 2), ""))
                    if not raw_val:
                        raw_val = clean_value(cell_map.get((r, 3), ""))
                        
                    if raw_val:
                        item_id = f"{test_name}_{file_key}".replace(" ", "_")
                        item = {
                            'user_id': user_id,
                            'record_id': item_id, 
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
        raise e