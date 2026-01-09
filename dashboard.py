import boto3
import csv
import os
import io
import time 

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

TABLE_NAME = os.environ['DYNAMODB_TABLE']

def lambda_handler(event, context):
    try:
        record = event['Records'][0]
        bucket_name = record['s3']['bucket']['name']
        file_key = record['s3']['object']['key']
        
        print(f"📂 Trigger received for: {file_key} in {bucket_name}")

        response = s3.get_object(Bucket=bucket_name, Key=file_key)
        file_content = response['Body'].read().decode('utf-8')
        
        csv_file = io.StringIO(file_content)
        reader = csv.DictReader(csv_file)
        
        table = dynamodb.Table(TABLE_NAME)
        
        try:
            user_id = file_key.split('/')[1] 
        except:
            user_id = "unknown_user"

        timestamp = str(int(time.time())) 

        with table.batch_writer() as batch:
            for row in reader:
                metric_name = row['Metric']
                record_id = f"{metric_name}_{file_key}"
                
                item = {
                    'user_id': user_id,
                    'record_id': record_id,
                    'metric': metric_name,
                    'value': row['Value'],
                    'unit': row['Unit'],
                    'range_low': row['Range_Low'],
                    'range_high': row['Range_High'],
                    'source_file': file_key,
                    'upload_timestamp': timestamp 
                }
                
                batch.put_item(Item=item)
                print(f"   -> Saved: {metric_name}")

        return {"statusCode": 200, "body": "Success"}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise e