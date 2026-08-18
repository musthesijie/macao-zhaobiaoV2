# 澳门招标 V2

澳门全部公共机构公开招标资讯的自动收录、检索和展示网站。数据摘自法务局《澳门特别行政区公报》的[公开竞投（服务提供／物品供应）](https://www.bo.dsaj.gov.mo/cn/news/list/b/?d=13&c=&y=)事项分类。

V2 沿用原版 [`macao-zhaobiao`](https://github.com/musthesijie/macao-zhaobiao) 的视觉系统。采集器自动遍历公报事项分类的全部分页，以公报链接去重、按公共机构分组，并在看板为每个机构展示最新五则信息。

机构分类以澳门政府入口网站的[公共部门及其他机构](https://www.gov.mo/zh-hant/about-government/departments-and-agencies/)目录为准；公报中没有列入该目录的历史机构或其他实体统一归入“其他”。列表会自动移除“公告一则／通告一则”等公文套语。

右上角“资料来源”目录及机构卡片的采购网址取自原版 `macao-zhaobiao` 的已核对来源清单，并保存在 `config/source-urls.json`，方便部署时稳定读取和后续维护。

## Docker 一键部署

```bash
docker compose up -d --build
```

打开 `http://服务器地址:8080`。SQLite 数据保存在 Docker 卷 `tender-data`，重建容器不会丢失。服务启动时立即同步，之后默认每 12 小时同步一次。

可在项目目录创建 `.env`：

```env
PORT=8080
REFRESH_INTERVAL=43200
REFRESH_TOKEN=请设置一个随机管理令牌
FETCH_WORKERS=4
TENDER_SOURCE_URL=https://www.bo.dsaj.gov.mo/cn/news/list/b/?d=13&c=&y=
```

设置 `REFRESH_TOKEN` 后，手动刷新接口需提供 `Authorization: Bearer <令牌>`；同时网页上的公开刷新按钮将无法使用，适合生产环境。定时刷新不受影响。

## 接口

* `GET /api/dashboard`：全部机构、每机构最新五则及全站最新十则
* `GET /api/tenders?agency=&q=`：按机构或关键词检索公报事项
* `POST /api/refresh`：立即遍历并刷新公报事项分类
* `GET /healthz`：容器健康检查

## 本地运行

项目只使用 Python 标准库，无需安装依赖：

```bash
python app.py
```
