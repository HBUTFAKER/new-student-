# 出事了怎么办（应急速查卡）

> 用法：报警了 / 客户说打不开了 → 打开这张卡 → 按「症状」找到对应段落 → 复制命令跑 → 把输出贴给我。
> 你不需要看懂输出，只需要把它贴过来。

---

## 三条铁律

1. **先备份，再动手。** 只要能连上服务器，第一件事是导一份数据出来。宁可贵 10 分钟，不要赌一把。
2. **只做「重启级」操作。** 重启服务、重启容器、清日志 —— 这三种可以自己干。其余一律停手问我。
3. **不确定就停。** 删东西、改配置、重装系统之前，先问我一句。误操作的代价比等 10 分钟贵得多。

---

## 第一步：先搞清楚「哪一层坏了」

按顺序跑这三条，把输出全贴给我：

```bash
# 1. 服务器还活着吗（能返回就说明机器和网络没问题）
uptime && df -h && free -h

# 2. 应用还活着吗（返回 {"status":"ok"} 就是活着）
curl -s -m 5 http://127.0.0.1:8000/health

# 3. 容器在跑吗
docker ps
```

三条输出一贴，我基本能判断问题在哪一层。

---

## 症状 → 命令对照

### ① 网页/小程序打不开

```bash
curl -s -m 5 http://127.0.0.1:8000/health
docker ps
docker logs --tail 100 student-management-api-1 2>&1 | tail -50
```

- `/health` 不通 → 应用挂了，往下看 ②
- `/health` 通但外网打不开 → 域名/证书/防火墙问题，把 `curl` 结果和浏览器报错截图给我

### ② 应用挂了（`/health` 不通）

```bash
docker compose ps
docker compose restart api
sleep 5 && curl -s -m 5 http://127.0.0.1:8000/health
```

重启后还是不通 → 贴 `docker compose logs --tail 200 api` 给我，**别自己改配置**。

### ③ 报 500 / 功能报错

```bash
docker compose logs --tail 200 api 2>&1 | grep -A 30 "Traceback\|ERROR" | tail -60
```

把这段贴给我。这是代码问题，我改，不用你动服务器。

### ④ 磁盘满了（`df -h` 显示 Use% 90%+）

```bash
# 先看谁占的
du -sh /var/lib/docker/* 2>/dev/null | sort -h | tail -5
du -sh /var/log/* 2>/dev/null | sort -h | tail -5

# 清掉旧的容器日志（安全，只删日志）
sudo truncate -s 0 /var/lib/docker/containers/*/*-json.log

# 清掉无用的旧镜像（安全，不影响运行中的）
docker image prune -af
```

**不要删 `/var/lib/docker/volumes` 里的东西** —— 那是数据库数据。

### ⑤ 数据库连不上（日志里有 `connection refused` / `could not connect`）

```bash
docker compose ps db
docker compose logs --tail 100 db
df -h    # 磁盘满也会导致 PG 起不来
```

贴给我。**别自己执行任何 SQL**，尤其是 `DROP` / `TRUNCATE` / `DELETE`。

### ⑥ SSH 都连不上

只能走云控制台：

1. 登录阿里云/腾讯云控制台 → 找到这台服务器
2. 用「远程连接 / VNC 登录」网页终端进
3. 进去后先跑第一步的三条命令

如果控制台显示实例「运行中」但你连不上 → 可能是云厂商网络问题，看控制台的「实例监控」和「公告」。

---

## 发给我什么（直接抄这个模板）

```
【现象】具体看到什么：网页打不开 / 报错 / 卡住不动
【开始时间】大概几点，之前正常吗
【影响范围】所有人都这样，还是只有一个人
【最近改过什么】发过版吗？改过配置吗？交过费吗？
【命令输出】
（把第一步三条命令的输出贴这里）
【截图】有报错页面就截图
```

**最有用的一句话是「刚才还是好的，我做了什么什么之后就坏了」** —— 有这句能省一半时间。

---

## 绝对不能做的事

| 别做 | 为什么 |
|---|---|
| `rm -rf` 任何路径 | 删错一个字母，整台机器没了 |
| `DROP DATABASE` / `TRUNCATE` / `DELETE FROM xxx` | 数据没了就真没了，我能改代码但改不回数据 |
| 把数据库端口 5432 开到公网 | 几小时内就会被扫描、爆破、勒索 |
| 不看日志就重装系统 | 90% 的问题重启容器就能解决 |
| 在客户催你的时候动手改配置 | 越急越出错，先重启止血，再慢慢查 |
| 把服务器密码发给陌生人 | 包括自称「技术支持」的 |

---

## 备份（唯一的保命符）

改动前、发版前、任何不确定的时候，先跑这个：

```bash
cd /path/to/student-management
docker compose exec -T db pg_dump -U student student > backup_$(date +%Y%m%d_%H%M).sql
ls -lh backup_*.sql
```

看到 `.sql` 文件有大小（不是 0 字节）才算成功。

把备份文件下载到本地电脑（`scp` 或者云控制台的文件传输），别只留在服务器上 —— 服务器挂了备份也跟着没。

**每月至少验证一次**：把最近的备份导到一个空库，能导进去、能查到数据，才算真的备份有效。
