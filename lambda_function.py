import boto3
import os
import json
import urllib.parse
import time
import base64
import re
from datetime import datetime

s3 = boto3.client('s3')
bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb')

TABLE_NAME = os.environ['DYNAMODB_TABLE']

def analyze_document_with_claude(bucket, key):
    print(f"🧠 Processing {key} with Claude 3 Haiku...")
    
   
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        file_bytes = response['Body'].read()
        encoded_pdf = base64.b64encode(file_bytes).decode('utf-8')
    except Exception as e:
        print(f"❌ S3 Read Error: {e}")
        return []

   
    prompt = """
    You are a medical data parser. Look at the attached blood work PDF.
    
    TASK:
    Extract all blood test results into a clean JSON list.
    
    RULES:
    1. IGNORE: Reference ranges, notes, "page x of y", doctor names, addresses.
    2. TARGET: Only the Metric Name, the numeric Value, and the Unit.
    3. DATE: Find the "Collection Date" or "Service Date". If not found, use "UNKNOWN".
    4. NORMALIZE NAMES (Strictly follow this):
       - "Testosterone, Free and Total" -> "Testosterone, Total" (if it's the total value)
       - "Testosterone, Free" -> "Testosterone, Free"
       - "Estradiol, Ultrasensitive, LC/MS" -> "Estradiol, Ultrasensitive"
       - "Vitamin D, 25-Hydroxy" -> "Vitamin D"
       - Remove "MS", "LC/MS", "Dialysis" unless it distinguishes the test.
    
    OUTPUT FORMAT:
    Return ONLY a JSON list. No markdown formatting, no conversational text.
    [
      { "metric": "Testosterone, Total", "value": 750, "unit": "ng/dL", "date": "2024-01-01" },
      { "metric": "Estradiol", "value": 25, "unit": "pg/mL", "date": "2024-01-01" }
    ]
    """

   
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": encoded_pdf
                        }
                    },
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

       
        results = analyze_document_with_claude(bucket, key)
        
        
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