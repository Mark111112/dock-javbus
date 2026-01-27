# 第一阶段：构建依赖
FROM python:3.9-alpine as builder

# 安装编译依赖
RUN apk add --no-cache gcc musl-dev jpeg-dev zlib-dev libjpeg

# 复制并安装Python依赖
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# 第二阶段：创建最终镜像
FROM python:3.9-alpine

# 设置工作目录
WORKDIR /app

# 复制第一阶段安装的Python包
COPY --from=builder /install /usr/local

# 安装运行时依赖，尽量保持精简
# 安装运行时依赖与 ffmpeg（用于下载转存 HLS 为 MP4）
RUN apk add --no-cache libjpeg ffmpeg
RUN apk add --no-cache intel-media-driver libva-utils
RUN apk add --no-cache intel-media-sdk

# 复制应用文件
COPY *.py .
COPY config/ /app/config/
COPY templates/ /app/templates/
COPY static/ /app/static/
COPY modules/ /app/modules/

# 创建必要的目录
RUN mkdir -p /app/buspic /app/data

# 设置容器时区
RUN cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo 'Asia/Shanghai' > /etc/timezone

# 安装额外的依赖 - chardet
RUN pip install --no-cache-dir chardet

# 暴露端口
EXPOSE 8080

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV LANG=zh_CN.UTF-8
ENV LC_ALL=zh_CN.UTF-8

# 启动命令 
CMD ["python", "webserver.py"] 