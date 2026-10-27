# 协议与兼容范围

本工具使用 HTTP JSON 接口访问目标云服务。客户端参数由本机配置提供，服务地址默认使用 HTTPS，并保持证书验证。HTTP 只允许用于本机测试地址。

## 设备数据

- 设备标识为 `devId`，功能状态来自 `dps` 或 `dataPointInfo.dps`。
- `dataPointInfo.dpName` / `dpName` 将功能点映射到自定义名称。
- 产品功能定义包含 `code`、功能点 ID、读写模式，以及布尔值、枚举值或带步长和缩放的小数数值范围。
- 页面控件由产品功能定义决定。目标温度和位置遵守范围与步长；亮度百分比换算到相应通道的数值范围。
- 网关、管理参数及未识别功能没有任意写入入口。
- 普通状态刷新保留当前功能定义和通道名称，完整发现会重新读取产品定义。

## 请求与会话

请求采用目标服务所要求的字段编码和消息认证。签名仅用于协议兼容，不把签名密钥嵌入公开源码。请求日志省略账号、密码、会话、认证参数与完整响应。

程序为每个登录会话复用连接池，并直接连接配置的服务，避免 Windows 自动代理绕过检查对每次请求造成额外延迟。会话过期时合并重新登录请求，避免多个并发读取重复认证。

后台设备发现限制为最多 4 个并发详情读取。注销或停止服务会取消相关任务。定时状态读取不占用设备控制锁。

命令发送后再读取状态，只有命令发出后的读取值匹配才标记为确认。场景属于触发请求，不依据重复的旧值宣告现场执行成功。云端状态不是现场运行的保证。

## 文档

以下资料用于解释设备字段和客户端库行为，代码不包含相应 SDK：

- [设备模型和功能状态字段](https://developer.tuya.com/en/docs/app-development/devicemanage?id=Ka6ki8r2rfiuu)
- [功能点与自定义名称](https://developer.tuya.com/en/docs/iot/equipment-related-interface?id=K9m1dlii6zkf7)
- [场景开关的标准功能定义](https://developer.tuya.com/en/docs/iot/s?id=K9gf7o5o3iuww)
- [aiohttp 客户端会话](https://docs.aiohttp.org/en/stable/client_reference.html)
