import boto3
import os
import json
import urllib.parse
import time
import io
import re
from datetime import datetime
from pypdf import PdfReader

s3 = boto3.client('s3')
bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb')

TABLE_NAME = os.environ['DYNAMODB_TABLE']

def extract_text_from_pdf(bucket, key):
    print(f"📄 Extracting text from {key}...")
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        file_content = response['Body'].read()
        
        pdf = PdfReader(io.BytesIO(file_content))
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
        return full_text
    except Exception as e:
        print(f"❌ PDF Read Error: {e}")
        return None

def analyze_with_claude(text_content, file_key):
    print("🧠 Sending text to Claude 3 Haiku...")
    
    prompt = f"""
    You are a medical data parser.
    
    TASK:
    Extract blood test results from the provided OCR text into a JSON list.
    
    RULES:
    1. IGNORE: Reference ranges, notes, "page x of y", doctor names, addresses.
    2. TARGET: Only the Metric Name, the numeric Value, and the Unit.
    3. DATE: Find the "Collection Date" or "Service Date". If not found, use "UNKNOWN".
    4. NORMALIZE NAMES:
       - "Testosterone, Free and Total" -> "Testosterone, Total" (if it's the total value)
       - "Testosterone, Free" -> "Testosterone, Free"
       - "Estradiol, Ultrasensitive, LC/MS" -> "Estradiol, Ultrasensitive"
       - "Vitamin D, 25-Hydroxy" -> "Vitamin D"
    
    OUTPUT FORMAT:
    Return ONLY a valid JSON list. No markdown, no conversational text.
    [
      {{ "metric": "Testosterone, Total", "value": 750, "unit": "ng/dL", "date": "2024-01-01" }}
    ]

    DATA:
    {text_content}
    """

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text", 
                        "text": prompt
                    }
                ]
            }
        ]
    })

    try:
        response = bedrock.invoke_model(
            modelId="anthropic.claude-3-haiku-20240307-v1:0",
            body=body
        )
        response_body = json.loads(response.get("body").read())
        result_text = response_body['content'][0]['text']
        
        start = result_text.find('[')
        end = result_text.rfind(']') + 1
        if start != -1 and end != -1:
            return json.loads(result_text[start:end])
        else:
            print("❌ No JSON found in response")
            return []
            
    except Exception as e:
        print(f"❌ Bedrock Error: {e}")
        return []

def lambda_handler(event, context):
    try:
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        
        if not key.endswith('.pdf'):
            return {"statusCode": 200, "body": "Skipped non-PDF"}

       
        raw_text = extract_text_from_pdf(bucket, key)
        if not raw_text:
            return {"statusCode": 500, "body": "Failed to read PDF"}

        
        results = analyze_with_claude(raw_text, key)
        
        table = dynamodb.Table(TABLE_NAME)
        parts = key.split('/')
        user_id = parts[1] if len(parts) > 1 else "unknown"
        upload_time = str(int(time.time()))

        with table.batch_writer() as batch:
            for item in results:
                
                date_ts = upload_time
                if item.get('date') and item['date'] != "UNKNOWN":
                    try:
                        for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y"]:
                            try:
                                dt = datetime.strptime(item['date'], fmt)
                                date_ts = str(int(dt.timestamp()))
                                break
                            except:
                                continue
                    except:
                        pass 

                val_str = str(item['value'])
                clean_val = re.sub(r'[^\d\.]', '', val_str) if any(c.isdigit() for c in val_str) else val_str

                record_id = f"{item['metric'].replace(' ', '_')}_{key}"
                
                print(f"   -> Saving {item['metric']}: {clean_val}")
                
                batch.put_item(Item={
                    'user_id': user_id,
                    'record_id': record_id,
                    'metric': item['metric'],
                    'value': clean_val,
                    'original_value': val_str,
                    'unit': item.get('unit', ''),
                    'source_file': key,
                    'upload_timestamp': date_ts
                })

        return {"statusCode": 200, "body": "Success"}
    except Exception as e:
        print(f"Error: {e}")
        raise e