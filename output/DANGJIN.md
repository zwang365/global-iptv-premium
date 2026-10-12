# DangJin补充来源检查

列表读取成功，共180条；仅重点抽测CCTV1、CCTV5与宁夏卫视，不宣称180条均已实测。

|频道|检测结果|处理|
|---|---|---|
|CCTV-1|URLError|未收录|
|CCTV-5|URLError|未收录|
|宁夏卫视|http_403|未收录|

CCTV1/5为IPv6运营商地址，当前直连连接失败；且未确认面向普通公网的分发许可，不能自动当作全国公共直播。宁夏卫视HTTP403仅表示访问失败，不能单独据此断言地区封锁。其他特殊AuthInfo地址不进入无鉴权订阅。来源：https://raw.githubusercontent.com/DangJin/awesome-iptv/refs/heads/main/IPTV.m3u
