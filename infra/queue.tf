resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-tasks-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "tasks" {
  name = "${var.name}-tasks"

  # A task that is not acked within 10 minutes is assumed lost (controller died) and redelivered.
  visibility_timeout_seconds = 600
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}
