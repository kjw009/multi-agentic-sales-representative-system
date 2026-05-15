# S3 image hosting setup

eBay's Inventory and Trading APIs fetch listing images from a public URL. The
internal MinIO endpoint (`http://minio:9000/...`) is not reachable from eBay,
so production must serve images from a public S3 bucket.

## One-time provisioning

Replace `salesrep-images-prod` with whatever bucket name you want. Bucket names
are globally unique, so pick something namespaced. The eBay seller account is
UK-based, so the bucket lives in `eu-west-2` for latency.

```bash
BUCKET=salesrep-images-prod
REGION=eu-west-2

# 1. Create the bucket
aws s3api create-bucket \
  --bucket "$BUCKET" \
  --region "$REGION" \
  --create-bucket-configuration "LocationConstraint=$REGION"

# 2. Allow public policies (S3 blocks public access by default since 2023)
aws s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false"

# 3. Attach a public-read bucket policy (GetObject only)
cat > /tmp/bucket-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::$BUCKET/*"
    }
  ]
}
EOF
aws s3api put-bucket-policy --bucket "$BUCKET" --policy file:///tmp/bucket-policy.json

# 4. (Optional) CORS, so the web app can fetch directly without a proxy
cat > /tmp/cors.json <<EOF
{
  "CORSRules": [
    {
      "AllowedOrigins": ["https://devopslearn.store", "http://localhost:3000"],
      "AllowedMethods": ["GET"],
      "AllowedHeaders": ["*"],
      "MaxAgeSeconds": 3000
    }
  ]
}
EOF
aws s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration file:///tmp/cors.json
```

## IAM permission for the EC2 instance role

The EC2 host's instance role needs `s3:PutObject` on the new bucket — that is
all the upload path uses. Attach this inline policy to the role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::salesrep-images-prod/*"
    }
  ]
}
```

## Production env vars

Set these on the EC2 host (or in SSM Parameter Store, or wherever the prod
`.env` lives — **not** in the repo):

```bash
# Leave empty so boto3 hits real AWS S3 (don't point at MinIO)
S3_ENDPOINT_URL=
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_BUCKET=salesrep-images-prod
S3_REGION=eu-west-2
# The host eBay (and buyers) will fetch images from:
S3_PUBLIC_BASE_URL=https://salesrep-images-prod.s3.eu-west-2.amazonaws.com
```

Leaving `S3_ACCESS_KEY` / `S3_SECRET_KEY` empty causes boto3 to pick up the
EC2 instance role automatically — preferred over baking long-lived keys into
the env file.

## Local dev (unchanged)

Local `.env` still uses MinIO. Leave `S3_PUBLIC_BASE_URL` empty and image URLs
fall back to `${S3_ENDPOINT_URL}/${S3_BUCKET}/{key}` (i.e. MinIO). eBay calls
won't work locally — that's expected; image hosting only matters for prod
publishes.

## Verifying

After deploying:

```bash
# Upload a tiny test object, confirm it's publicly fetchable
echo hello > /tmp/test.txt
aws s3 cp /tmp/test.txt "s3://$BUCKET/test.txt"
curl -I "https://$BUCKET.s3.$REGION.amazonaws.com/test.txt"
# Expect HTTP/1.1 200 OK
aws s3 rm "s3://$BUCKET/test.txt"
```

Then publish an item through the app and check that the `eBay create_inventory_item`
log line shows a `https://...amazonaws.com/...` URL (not `http://minio:...`).
