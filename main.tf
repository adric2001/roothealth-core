terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 4.16"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.2"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_cognito_user_pool" "users" {
  name = "roothealth-users"

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = true
  }

  username_attributes = ["email"]
  auto_verified_attributes = ["email"]
}

resource "aws_cognito_user_pool_client" "client" {
  name = "roothealth-app-client"
  user_pool_id = aws_cognito_user_pool.users.id
  
  generate_secret = false 
  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH", 
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH"
  ]
}

resource "aws_s3_bucket" "raw_data" {
  bucket = "roothealth-raw-files-adric"
  force_destroy = true 
}

resource "aws_s3_bucket_public_access_block" "raw_data_block" {
  bucket = aws_s3_bucket.raw_data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "health_stats" {
  name           = "RootHealth_Stats"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "user_id"    
  range_key      = "record_id"  

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "record_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "supplements" {
  name           = "RootHealth_Supplements"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "user_id"
  range_key      = "item_name" # Sort by drug/supplement name

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "item_name"
    type = "S"
  }
}

output "supplements_table_name" {
  value = aws_dynamodb_table.supplements.name
}

resource "aws_iam_role" "ingestion_role" {
  name = "roothealth_ingestion_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "ingestion_policy" {
  name = "roothealth_ingestion_policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["dynamodb:BatchWriteItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.health_stats.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "${aws_s3_bucket.raw_data.arn}",
          "${aws_s3_bucket.raw_data.arn}/*"
        ]
      },
     
      {
        Effect = "Allow"
        Action = [
          "textract:AnalyzeDocument",       # For single page (legacy)
          "textract:StartDocumentAnalysis", # For multi-page PDF start
          "textract:GetDocumentAnalysis"    # For checking results
        ]
        Resource = "*"
      },
      
      {
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "attach_ingestion" {
  role       = aws_iam_role.ingestion_role.name
  policy_arn = aws_iam_policy.ingestion_policy.arn
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "lambda_function.py"
  output_path = "lambda_function.zip"
}

resource "aws_lambda_function" "ingestor" {
  filename      = "lambda_function.zip"
  function_name = "RootHealthIngestor"
  role          = aws_iam_role.ingestion_role.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.9"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout = 60

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.health_stats.name
    }
  }
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowExecutionFromS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_data.arn
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket = aws_s3_bucket.raw_data.id
  lambda_function {
    lambda_function_arn = aws_lambda_function.ingestor.arn
    events              = ["s3:ObjectCreated:*"]
  }
  depends_on = [aws_lambda_permission.allow_s3]
}

resource "aws_ecr_repository" "app_repo" {
  name                 = "roothealth-dashboard"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

resource "aws_iam_role" "apprunner_role" {
  name = "roothealth_apprunner_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "apprunner_policy" {
  name = "roothealth_apprunner_policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.health_stats.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "attach_apprunner" {
  role       = aws_iam_role.apprunner_role.name
  policy_arn = aws_iam_policy.apprunner_policy.arn
}

resource "aws_iam_role" "apprunner_access_role" {
  name = "roothealth_apprunner_access_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "apprunner_access_attach" {
  role       = aws_iam_role.apprunner_access_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

resource "aws_apprunner_service" "dashboard" {
  service_name = "roothealth-dashboard"

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access_role.arn
    }
    auto_deployments_enabled = true 
    image_repository {
      image_identifier      = "${aws_ecr_repository.app_repo.repository_url}:latest"
      image_repository_type = "ECR"
      image_configuration {
        port = "8080"
        runtime_environment_variables = {
          DYNAMODB_TABLE       = aws_dynamodb_table.health_stats.name
          AWS_REGION           = "us-east-1"
          COGNITO_USER_POOL_ID = aws_cognito_user_pool.users.id
          COGNITO_CLIENT_ID    = aws_cognito_user_pool_client.client.id
        }
      }
    }
  }
  instance_configuration {
    instance_role_arn = aws_iam_role.apprunner_role.arn
  }
  health_check_configuration {
    protocol = "TCP"
    interval = 10
    timeout  = 5
  }
  depends_on = [aws_iam_role_policy_attachment.apprunner_access_attach]
}

output "s3_bucket_name" { value = aws_s3_bucket.raw_data.id }
output "dynamodb_table_name" { value = aws_dynamodb_table.health_stats.name }
output "ecr_url" { value = aws_ecr_repository.app_repo.repository_url }
output "dashboard_url" { value = aws_apprunner_service.dashboard.service_url }
output "cognito_user_pool_id" { value = aws_cognito_user_pool.users.id }
output "cognito_client_id" { value = aws_cognito_user_pool_client.client.id }