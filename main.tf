terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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
  explicit_auth_flows = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
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
  range_key      = "item_name" 
  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "item_name"
    type = "S"
  }
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
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = ["${aws_s3_bucket.raw_data.arn}", "${aws_s3_bucket.raw_data.arn}/*"]
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "aws-marketplace:ViewSubscriptions",
          "aws-marketplace:Subscribe",
          "aws-marketplace:Unsubscribe"
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
  source_dir = "lambda_package"
  output_path = "lambda_function.zip"
}

resource "aws_lambda_function" "ingestor" {
  filename      = "lambda_function.zip"
  function_name = "RootHealthIngestor"
  role          = aws_iam_role.ingestion_role.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11" 
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

resource "aws_elastic_beanstalk_application" "app" {
  name        = "roothealth-core"
  description = "RootHealth Streamlit Dashboard"
}

resource "aws_iam_role" "eb_instance_role" {
  name = "roothealth_eb_instance_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_instance_profile" "eb_instance_profile" {
  name = "roothealth_eb_instance_profile"
  role = aws_iam_role.eb_instance_role.name
}

resource "aws_iam_role_policy_attachment" "eb_web_tier" {
  role       = aws_iam_role.eb_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/AWSElasticBeanstalkWebTier"
}

resource "aws_iam_role_policy_attachment" "eb_docker" {
  role       = aws_iam_role.eb_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/AWSElasticBeanstalkMulticontainerDocker"
}

resource "aws_iam_role_policy_attachment" "eb_ecr_read" {
  role       = aws_iam_role.eb_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy" "eb_app_permissions" {
  name = "roothealth_eb_custom_policy"
  role = aws_iam_role.eb_instance_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = [aws_dynamodb_table.health_stats.arn, aws_dynamodb_table.supplements.arn]
      },
      {
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.raw_data.arn}/*"
      }
    ]
  })
}

resource "aws_elastic_beanstalk_environment" "env" {
  name                = "RoothealthCore-env"
  application         = aws_elastic_beanstalk_application.app.name
  
  solution_stack_name = "64bit Amazon Linux 2023 v4.9.0 running Docker"
  
  lifecycle {
    ignore_changes = [version_label]
  }
  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "IamInstanceProfile"
    value     = aws_iam_instance_profile.eb_instance_profile.name
  }
  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "EnvironmentType"
    value     = "SingleInstance" 
  }
  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "DYNAMODB_TABLE"
    value     = aws_dynamodb_table.health_stats.name
  }
  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "AWS_REGION"
    value     = "us-east-1"
  }
  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "COGNITO_USER_POOL_ID"
    value     = aws_cognito_user_pool.users.id
  }
  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "COGNITO_CLIENT_ID"
    value     = aws_cognito_user_pool_client.client.id
  }
  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "S3_BUCKET_NAME"
    value     = aws_s3_bucket.raw_data.id
  }
  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "INVITE_CODE"
    value     = "PLACEHOLDER_MANAGED_BY_GITHUB" 
  }
  lifecycle {
    ignore_changes = [
      version_label,
      # Add this line so Terraform doesn't revert your GitHub Secret
      setting 
    ]
  }
}


output "eb_cname" { value = aws_elastic_beanstalk_environment.env.cname }
output "ecr_url" { value = aws_ecr_repository.app_repo.repository_url }