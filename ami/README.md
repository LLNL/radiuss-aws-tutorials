# Create AMIs
This folder has a script to help create an AMI from a base image and a docker image.

Using tagged subnet and security group set up below:
``` bash
SUBNET_ID=$(aws ec2 describe-subnets \
  --filters "Name=tag:Purpose,Values=ImageBuilder" \
  --query 'Subnets[0].SubnetId' --output text)

SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=tag:Purpose,Values=ImageBuilder" \
  --query 'SecurityGroups[0].GroupId' --output text)
```

To start from a GPU-optimized AMI with ECS and Docker installed:
``` bash
BASE_AMI=$(aws ssm get-parameters \
  --names /aws/service/ecs/optimized-ami/amazon-linux-2023/gpu/recommended/image_id \
  --region us-east-1 \
  --query "Parameters[0].Value" \
  --output text)
```

Example usage of AMI builder:
```bash
./build-ami.sh $BASE_AMI "raja-tutorial" $SUBNET_ID $SG_ID <my-key-pair> ghcr.io/llnl/raja-suite-tutorial/tutorial:latest raja-suite-tutorial:local g4dn.8xlarge
```

The AMI ID is automatically stored in Parameter Store as `/hpcic-tutorials/amis/<ami-name>`.
You can retrieve it later with: `aws ssm get-parameter --name "/hpcic-tutorials/amis/<tutorial-name>-tutorial"`

## Prerequisites
One time setup of the IAM role, subnet, and security group.

### Create the IAM role
```
aws iam create-role \
  --role-name EC2InstanceProfileForImageBuilder \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

# Attach the required policy
aws iam attach-role-policy \
  --role-name EC2InstanceProfileForImageBuilder \
  --policy-arn arn:aws:iam::aws:policy/EC2InstanceProfileForImageBuilder

# Create the instance profile
aws iam create-instance-profile \
  --instance-profile-name EC2InstanceProfileForImageBuilder

# Add the role to the instance profile
aws iam add-role-to-instance-profile \
  --instance-profile-name EC2InstanceProfileForImageBuilder \
  --role-name EC2InstanceProfileForImageBuilder

# Add the SSM managed instance policy
aws iam attach-role-policy \
  --role-name EC2InstanceProfileForImageBuilder \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  ```

### Create subnet
```
# Get your default VPC ID
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=is-default,Values=true" --query 'Vpcs[0].VpcId' --output text)

# Create subnet in first AZ
AZ=$(aws ec2 describe-availability-zones --query 'AvailabilityZones[0].ZoneName' --output text)

SUBNET_ID=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID \
  --cidr-block 172.31.64.0/20 \
  --availability-zone $AZ \
  --tag-specifications 'ResourceType=subnet,Tags=[
    {Key=Name,Value=ImageBuilder-Public-Subnet},
    {Key=Purpose,Value=ImageBuilder},
    {Key=Type,Value=Public}
  ]' \
  --query 'Subnet.SubnetId' --output text)

# Enable auto-assign public IP
aws ec2 modify-subnet-attribute \
  --subnet-id $SUBNET_ID \
  --map-public-ip-on-launch

echo "Created subnet: $SUBNET_ID"
```

### Create security group
```
SG_ID=$(aws ec2 create-security-group \
  --group-name ImageBuilder-SG \
  --description "Security group for EC2 Image Builder" \
  --vpc-id $VPC_ID \
  --tag-specifications 'ResourceType=security-group,Tags=[
    {Key=Name,Value=ImageBuilder-SecurityGroup},
    {Key=Purpose,Value=ImageBuilder}
  ]' \
  --query 'GroupId' --output text)

# Add outbound HTTPS rule (for Docker Hub, package downloads)
aws ec2 authorize-security-group-egress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 443 \
  --cidr 0.0.0.0/0

# Add outbound HTTP rule (for package repositories)
aws ec2 authorize-security-group-egress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 80 \
  --cidr 0.0.0.0/0

# Optional: Add SSH inbound for debugging
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 22 \
  --cidr 0.0.0.0/0

echo "Created security group: $SG_ID"
```


# Clean up AMIs and associated resources
After deleting your stack, clean up the AMIs to avoid ongoing storage costs. Below are some helpful commands:
``` bash
# Replace "tutorial-name"
TUTORIAL_NAME="tutorial-name"
# Get AMI IDs
aws ec2 describe-images --owners self --filters "Name=name,Values=*${TUTORIAL_NAME}*" --query 'Images[*].[ImageId,Name,CreationDate]' --output table
# Get snapshot IDs (replace ami-id)
aws ec2 describe-images --image-ids <ami-id> --query 'Images[0].BlockDeviceMappings[*].Ebs.SnapshotId' --output text
# Deregister ami
aws ec2 deregister-image --image-id <ami-id>
# Delete snapshot
aws ec2 delete-snapshot --snapshot-id <snapshot_id>
# Delete SSM parameter
aws ssm delete-parameter --name "/hpcic-tutorials/amis/${TUTORIAL_NAME}-tutorial"
```

```
