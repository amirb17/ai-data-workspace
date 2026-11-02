"""Single-message SQS adapter. Never creates an SDK client or accesses credentials."""
from app.processing_worker.worker import QueueMessage


class SqsTransport:
    def __init__(self, client, queue_url, *, enabled=False, visibility_seconds=300):
        if not enabled:
            raise RuntimeError('SQS transport requires deployment approval')
        if not queue_url or type(visibility_seconds) is not int or not 1 <= visibility_seconds <= 900:
            raise ValueError('Invalid queue/visibility configuration')
        self.client, self.queue_url, self.visibility = client, queue_url, visibility_seconds

    def receive_one(self, stop):
        if stop.is_set():
            return None
        result = self.client.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=1,
            WaitTimeSeconds=10, VisibilityTimeout=self.visibility)
        messages = result.get('Messages', [])
        if len(messages) > 1:
            raise ValueError('Transport returned more than one message')
        if not messages:
            return None
        message = messages[0]
        return QueueMessage(message['MessageId'], message['Body'], message['ReceiptHandle'])

    def acknowledge(self, message):
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message.receipt_handle)

    def retry(self, message):
        self.client.change_message_visibility(QueueUrl=self.queue_url, ReceiptHandle=message.receipt_handle,
                                              VisibilityTimeout=30)

    def renew(self, message, lease):
        self.client.change_message_visibility(QueueUrl=self.queue_url, ReceiptHandle=message.receipt_handle,
                                              VisibilityTimeout=self.visibility)

    def close(self):
        self.client.close()
