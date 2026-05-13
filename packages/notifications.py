import boto3
from packages.config import settings

_sns = None

def _client():
    global _sns
    if _sns is None:
        _sns = boto3.client("sns", region_name=settings.aws_region)
    return _sns

def create_seller_topic(seller_id: str, email: str) -> str:
    """Create a per-seller SNS topic, subscribe their email, return topic ARN."""
    name = f"salesrep-seller-{seller_id}"
    arn = _client().create_topic(Name=name)["TopicArn"]
    _client().subscribe(TopicArn=arn, Protocol="email", Endpoint=email)
    return arn

def notify_seller(topic_arn: str, subject: str, message: str) -> None:
    """Publish a notification to the seller's topic. No-ops if SNS disabled or no topic."""
    if not settings.sns_enabled or not topic_arn:
        return
    _client().publish(TopicArn=topic_arn, Subject=subject, Message=message)

def delete_seller_topic(topic_arn: str) -> None:
    """Called on seller account deletion (GDPR erasure)."""
    if topic_arn:
        _client().delete_topic(TopicArn=topic_arn)
