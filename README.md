# 澳门招标 V2

澳门市政署公开招标资讯的自动收录、检索和展示网站。数据由[澳门政府数据开放平台](https://data.gov.mo/Detail?id=7da15d0f-968e-495c-971a-c30a05049884)提供。

## Docker 一键部署

```bash
docker compose up -d --build
```

打开 `http://服务器地址:8080`。SQLite 数据保存在 Docker 卷 `tender-data`，重建容器不会丢失。服务启动时立即同步，之后默认每 6 小时同步一次。

可在项目目录创建 `.env`：

```env
PORT=8080
REFRESH_INTERVAL=21600
REFRESH_TOKEN=请设置一个随机管理令牌
TENDER_APP_CODE=09d43a591fba407fb862412970667de4
```

设置 `REFRESH_TOKEN` 后，手动刷新接口需提供 `Authorization: Bearer <令牌>`；同时网页上的公开刷新按钮将无法使用，适合生产环境。定时刷新不受影响。

## 接口

* `GET /api/tenders?q=&year=&status=all&page=1&size=12`：检索及分页
* `GET /api/tenders/{编号}`：项目详情
* `GET /api/stats`：收录及同步状态
* `POST /api/refresh`：立即从官方 API 刷新
* `GET /healthz`：容器健康检查

## 本地运行

项目只使用 Python 标准库，无需安装依赖：

```bash
python app.py
```
