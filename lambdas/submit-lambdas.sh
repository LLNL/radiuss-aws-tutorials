#!/bin/bash

# launch function
zip launch-function.zip launch-function.py
aws s3 cp launch-function.zip s3://hpcic-tutorials-lambdas/slackbot-ec2/launch-function.zip
rm launch-function.zip

# notify function
mkdir -p notify-package
cd notify-package
python3 -m pip install requests -t .
cp ../notify-function.py .
zip -r ../notify-function.zip *
cd ..
aws s3 cp notify-function.zip s3://hpcic-tutorials-lambdas/slackbot-ec2/notify-function.zip
rm -rf notify-package
rm notify-function.zip

# reverse proxy function
mkdir -p reverse-proxy-package
cd reverse-proxy-package
python3 -m pip install urllib3 -t .
cp ../reverse-proxy-function.py .
zip -r ../reverse-proxy-function.zip *
cd ..
aws s3 cp reverse-proxy-function.zip s3://hpcic-tutorials-lambdas/slackbot-ec2/reverse-proxy-function.zip
rm -rf reverse-proxy-package
rm reverse-proxy-function.zip

# cleanup function
zip cleanup-tasks-function.zip cleanup-tasks-function.py
aws s3 cp cleanup-tasks-function.zip s3://hpcic-tutorials-lambdas/slackbot-ec2/cleanup-tasks-function.zip
rm cleanup-tasks-function.zip

aws s3api list-object-versions \
    --bucket hpcic-tutorials-lambdas \
    --prefix slackbot-ec2/ \
    --query 'Versions[?IsLatest==`true`].[Key,VersionId]' \
    --output table
