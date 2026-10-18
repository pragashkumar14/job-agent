output "lambda_name" {
  description = "Invoke manually with: aws lambda invoke --function-name <this> out.json"
  value       = aws_lambda_function.agent.function_name
}

output "table_name" {
  value = aws_dynamodb_table.jobs.name
}

output "schedule" {
  value = "${aws_scheduler_schedule.daily.schedule_expression} (${aws_scheduler_schedule.daily.schedule_expression_timezone})"
}

output "log_group" {
  description = "Tail with: aws logs tail <this> --follow"
  value       = aws_cloudwatch_log_group.lambda.name
}

output "next_steps" {
  value = <<-EOT

    1. Check your inbox and click the AWS SES verification link.
    2. Put the real secrets in Parameter Store (they are NOT in Terraform state):

       aws ssm put-parameter --name /job-agent/ANTHROPIC_API_KEY --type SecureString --overwrite --value "sk-ant-..."
       aws ssm put-parameter --name /job-agent/ADZUNA_APP_ID     --type SecureString --overwrite --value "..."
       aws ssm put-parameter --name /job-agent/ADZUNA_APP_KEY    --type SecureString --overwrite --value "..."

    3. Test it:

       aws lambda invoke --function-name ${aws_lambda_function.agent.function_name} --cli-binary-format raw-in-base64-out out.json ; type out.json

  EOT
}
