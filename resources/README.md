# 全球机场基础数据

2026-09-27 从 https://github.com/mborsetti/airportsdata 下载。

- airports.csv：机场、IATA/ICAO、城市、国家、坐标、IANA 时区。
- iata_macs.csv：该项目整理的 IATA 多机场城市分组。
- airportsdata-LICENSE.txt：上游 MIT 许可证，随数据保留。

启动时导入本机 data/airports.db；原始文件随项目分发，可离线重建。全部记录用于机场资料检索，只有存在 IATA 代码的记录可用于票价查询；有 IATA 不代表一定有在售定期航班。

多机场城市优先采用上游 MAC 名单，额外显式加入成都 CTU/TFU；其余按国家、行政区和相同城市名聚合，不按距离猜测都会圈。选择时显示实际机场名单。中文名只覆盖常用机场，其他使用原文，可搜索英文城市名和代码。数据不是实时航班时刻表，不保证所有机场都有票价接口覆盖。
