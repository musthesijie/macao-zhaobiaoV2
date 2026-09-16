# 澳门招标 V2

澳门全部公共机构公开招标资讯的自动收录、检索和展示网站。数据摘自法务局《澳门特别行政区公报》的[公开竞投（服务提供／物品供应）](https://www.bo.dsaj.gov.mo/cn/news/list/b/?d=13&c=&y=)事项分类。

“全澳最新发布”合并《公报》及[澳门公共采购平台](https://www.pcp.gov.mo/)的招标资讯并显示最新二十条；看板也自动收录[横琴粤澳深度合作区采购招标](https://www.hengqin.gov.cn/macao_zh_hans/zwgk/cgzb/index.html?page=1)栏目，显示横琴最新二十条采购招标资讯。

V2 沿用原版 [`macao-zhaobiao`](https://github.com/musthesijie/macao-zhaobiao) 的视觉系统。采集器自动遍历公报事项分类的全部分页，以公报链接去重、按公共机构分组，并在看板为每个机构展示最新五则信息。

机构分类以澳门政府入口网站的[公共部门及其他机构](https://www.gov.mo/zh-hant/about-government/departments-and-agencies/)目录为准；公报中没有列入该目录的历史机构或其他实体统一归入“其他”。列表会自动移除“公告一则／通告一则”等公文套语。

## Docker 一键部署

```bash
docker compose up -d --build
```

打开 `http://服务器地址:8080`。SQLite 数据保存在 Docker 卷 `tender-data`，重建容器不会丢失。服务启动时会立即同步一次，之后由容器内的后台调度器每 12 小时（43,200 秒）自动抓取澳门公报及横琴招标资讯；单次抓取即使遇到临时异常，调度器也会继续等待并执行下一轮任务。

可在项目目录创建 `.env`：

```env
PORT=8080
REFRESH_INTERVAL=43200
REFRESH_TOKEN=请设置一个随机管理令牌
FETCH_WORKERS=4
TENDER_SOURCE_URL=https://www.bo.dsaj.gov.mo/cn/news/list/b/?d=13&c=&y=
HENGQIN_API_URL=https://www.hengqin.gov.cn/postmeta/i/22178.json
PCP_API_URL=https://www.pcp.gov.mo/gpcms/web/front/v2/info/selectInfoMoreChannel
```

`REFRESH_INTERVAL` 的单位为秒；保留默认值 `43200` 即为每 12 小时刷新。修改 `.env` 后需执行 `docker compose up -d --build` 让配置生效。可通过看板上的“最后更新”时间或 `GET /api/dashboard` 返回的 `last_refresh` 字段确认自动抓取是否成功。

设置 `REFRESH_TOKEN` 后，手动刷新接口需提供 `Authorization: Bearer <令牌>`；同时网页上的公开刷新按钮将无法使用，适合生产环境。定时刷新不受影响。

## 接口

* `GET /api/dashboard`：全部机构、每机构最新五则、全澳及横琴最新二十则
* `GET /api/tenders?agency=&q=`：按机构或关键词检索公报事项
* `POST /api/refresh`：立即刷新澳门公报事项及横琴最新采购招标
* `GET /healthz`：容器健康检查

## 本地运行

项目只使用 Python 标准库，无需安装依赖：

```bash
python app.py
```
