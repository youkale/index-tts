# S3 Public URL 配置示例

## 概述

S3兼容存储通常有两种URL：
- **API端点URL** (`S3_ENDPOINT_URL`) - 用于上传和管理文件
- **公共访问URL** (`S3_PUBLIC_URL`) - 用于公开访问文件，通常通过CDN或自定义域名

## 配置优先级

1. **S3_PUBLIC_URL** - 最高优先级，用于生成公共访问链接
2. **S3_ENDPOINT_URL** - 中等优先级，API操作和备用访问
3. **默认AWS格式** - 最低优先级，标准AWS S3格式

## 各服务配置示例

### 1. Cloudflare R2

#### 场景1：使用自定义域名
```bash
# API操作端点
S3_ENDPOINT_URL=https://abc123def456.r2.cloudflarestorage.com

# 公共访问域名（通过Cloudflare配置）
S3_PUBLIC_URL=https://cdn.yourdomain.com

# 其他配置
S3_ACCESS_KEY=your_r2_access_key
S3_SECRET_KEY=your_r2_secret_key
S3_BUCKET_NAME=my-audio-files
S3_REGION=auto
```

**生成的URL**: `https://cdn.yourdomain.com/task-uuid.wav`

#### 场景2：使用R2默认域名
```bash
# API操作端点
S3_ENDPOINT_URL=https://abc123def456.r2.cloudflarestorage.com

# 不设置公共URL，使用端点URL
# S3_PUBLIC_URL=

# 其他配置
S3_ACCESS_KEY=your_r2_access_key
S3_SECRET_KEY=your_r2_secret_key
S3_BUCKET_NAME=my-audio-files
S3_REGION=auto
```

**生成的URL**: `https://abc123def456.r2.cloudflarestorage.com/task-uuid.wav`

#### 场景3：使用Cloudflare Workers自定义域名
```bash
# API操作端点
S3_ENDPOINT_URL=https://abc123def456.r2.cloudflarestorage.com

# 通过Workers配置的自定义域名
S3_PUBLIC_URL=https://files.myapp.com

# 其他配置
S3_ACCESS_KEY=your_r2_access_key
S3_SECRET_KEY=your_r2_secret_key
S3_BUCKET_NAME=my-audio-files
S3_REGION=auto
```

**生成的URL**: `https://files.myapp.com/task-uuid.wav`

### 2. AWS S3

#### 场景1：标准S3（无自定义域名）
```bash
# 不设置端点URL，使用默认AWS
# S3_ENDPOINT_URL=
# S3_PUBLIC_URL=

# AWS配置
S3_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE
S3_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_BUCKET_NAME=my-tts-bucket
S3_REGION=us-east-1
```

**生成的URL**: `https://my-tts-bucket.s3.us-east-1.amazonaws.com/task-uuid.wav`

#### 场景2：S3 + CloudFront CDN
```bash
# 不设置端点URL，使用默认AWS
# S3_ENDPOINT_URL=

# CloudFront分发域名
S3_PUBLIC_URL=https://d1234567890.cloudfront.net

# AWS配置
S3_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE
S3_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_BUCKET_NAME=my-tts-bucket
S3_REGION=us-east-1
```

**生成的URL**: `https://d1234567890.cloudfront.net/task-uuid.wav`

### 3. MinIO

#### 场景1：本地MinIO
```bash
# MinIO API端点
S3_ENDPOINT_URL=http://localhost:9000

# MinIO公共访问URL（包含bucket名称）
S3_PUBLIC_URL=http://localhost:9000/audio-files

# MinIO配置
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET_NAME=audio-files
S3_REGION=us-east-1
```

**生成的URL**: `http://localhost:9000/audio-files/task-uuid.wav`

#### 场景2：生产环境MinIO + 反向代理
```bash
# MinIO API端点（内网）
S3_ENDPOINT_URL=http://minio.internal:9000

# 公共访问URL（通过Nginx等反向代理）
S3_PUBLIC_URL=https://files.mycompany.com

# MinIO配置
S3_ACCESS_KEY=production_access_key
S3_SECRET_KEY=production_secret_key
S3_BUCKET_NAME=tts-audio
S3_REGION=us-east-1
```

**生成的URL**: `https://files.mycompany.com/task-uuid.wav`

### 4. DigitalOcean Spaces

#### 场景1：标准Spaces
```bash
# Spaces API端点
S3_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com

# 不设置公共URL，使用端点URL
# S3_PUBLIC_URL=

# Spaces配置
S3_ACCESS_KEY=your_spaces_key
S3_SECRET_KEY=your_spaces_secret
S3_BUCKET_NAME=my-space
S3_REGION=nyc3
```

**生成的URL**: `https://nyc3.digitaloceanspaces.com/my-space/task-uuid.wav`

#### 场景2：Spaces + CDN
```bash
# Spaces API端点
S3_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com

# Spaces CDN端点
S3_PUBLIC_URL=https://my-space.nyc3.cdn.digitaloceanspaces.com

# Spaces配置
S3_ACCESS_KEY=your_spaces_key
S3_SECRET_KEY=your_spaces_secret
S3_BUCKET_NAME=my-space
S3_REGION=nyc3
```

**生成的URL**: `https://my-space.nyc3.cdn.digitaloceanspaces.com/task-uuid.wav`

## 配置最佳实践

### 1. 性能优化
- 使用CDN域名作为`S3_PUBLIC_URL`以提高访问速度
- 选择地理位置接近用户的存储区域

### 2. 安全考虑
- 确保公共URL配置正确的CORS策略
- 使用HTTPS协议保证传输安全
- 定期轮换访问密钥

### 3. 成本优化
- Cloudflare R2：零出站费用，适合大量下载
- AWS S3：考虑使用CloudFront减少直接访问费用
- MinIO：自托管，控制成本

### 4. 可用性
- 配置多区域备份
- 监控存储服务状态
- 设置告警通知

## 测试配置

### 验证配置脚本
```bash
#!/bin/bash
# test_s3_config.sh

echo "Testing S3 configuration..."

# 测试API端点连接
if [ -n "$S3_ENDPOINT_URL" ]; then
    echo "Testing API endpoint: $S3_ENDPOINT_URL"
    curl -I "$S3_ENDPOINT_URL" 2>/dev/null && echo "✓ API endpoint accessible" || echo "✗ API endpoint failed"
fi

# 测试公共URL（如果配置了）
if [ -n "$S3_PUBLIC_URL" ]; then
    echo "Testing public URL: $S3_PUBLIC_URL"
    curl -I "$S3_PUBLIC_URL" 2>/dev/null && echo "✓ Public URL accessible" || echo "✗ Public URL failed"
fi

echo "Configuration test completed."
```

### Python测试脚本
```python
import os
import boto3
from botocore.exceptions import ClientError

def test_s3_config():
    """测试S3配置"""
    try:
        # 初始化S3客户端
        s3_config = {
            'aws_access_key_id': os.getenv('S3_ACCESS_KEY'),
            'aws_secret_access_key': os.getenv('S3_SECRET_KEY'),
            'region_name': os.getenv('S3_REGION', 'us-east-1')
        }

        if os.getenv('S3_ENDPOINT_URL'):
            s3_config['endpoint_url'] = os.getenv('S3_ENDPOINT_URL')

        client = boto3.client('s3', **s3_config)

        # 测试连接
        response = client.list_buckets()
        print("✓ S3 connection successful")
        print(f"Available buckets: {[b['Name'] for b in response['Buckets']]}")

        # 测试存储桶访问
        bucket_name = os.getenv('S3_BUCKET_NAME')
        if bucket_name:
            try:
                client.head_bucket(Bucket=bucket_name)
                print(f"✓ Bucket '{bucket_name}' accessible")
            except ClientError as e:
                print(f"✗ Bucket '{bucket_name}' error: {e}")

    except Exception as e:
        print(f"✗ S3 configuration error: {e}")

if __name__ == "__main__":
    test_s3_config()
```

## 故障排除

### 常见问题

1. **公共URL无法访问**
   - 检查存储桶公共读取权限
   - 验证CORS配置
   - 确认CDN配置正确

2. **URL格式错误**
   - 确保URL不包含尾部斜杠
   - 验证域名解析正确
   - 检查协议（http/https）

3. **权限问题**
   - 验证访问密钥权限
   - 检查存储桶策略
   - 确认IAM角色配置

### 调试步骤

1. 验证基本连接
2. 测试文件上传
3. 检查生成的URL
4. 验证公共访问
5. 测试CDN缓存（如适用）
