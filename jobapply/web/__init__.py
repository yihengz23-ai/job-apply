"""面板的网页接口，按功能拆成 Flask 蓝图，由 app.py 注册。
清单：common 公共件（拦截、响应头、出错格式、读请求体、本机/隧道判断）＋页面、/api/health、/api/config、隧道地址、Gmail 授权｜mail_routes 写信、发送、定时、队列、网申投完记一笔、网申问答、附件｜board_routes 看板记录、统计、Excel、同步 Gmail、查回复｜ws_routes 网申待办、让助手填、读回 /api/wsreadback、wsfill.js｜agent_routes 助手对话｜profile_routes 简历预览和下载、网申资料包、网申底稿｜apps_routes 按申请成组的看板、单家详情、今天、进展口述和撤销、建议、志愿方式"""
