# K8s 部署清单
#
# 使用方法：
#   kubectl apply -f k8s/
#
# 或按需分步部署：
#   kubectl apply -f k8s/namespace.yaml
#   kubectl apply -f k8s/config.yaml      # ConfigMap + Secret
#   kubectl apply -f k8s/pvc.yaml
#   kubectl apply -f k8s/deployment.yaml
#   kubectl apply -f k8s/service.yaml
#   kubectl apply -f k8s/ingress.yaml     # 可选，需 Ingress Controller
#   kubectl apply -f k8s/network-policy.yaml
#
# 部署前必须修改：
#   1. config.yaml 中的 SG_ERM_SECRET_KEY（生成命令见注释）
#   2. deployment.yaml 中的 image 镜像地址
#   3. ingress.yaml 中的域名和 TLS 证书
