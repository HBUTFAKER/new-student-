#!/usr/bin/env bash
# 生产数据库备份：pg_dump 自定义格式（-Fc）按日期落盘 + 保留 N 份轮转。
#
# 密码一律从环境变量 / .env.prod 读取，本脚本不硬编码任何密码。
#
# 用法（在项目根目录）：
#   ./scripts/backup.sh                 # 默认保留 7 份，落到 ./backups
#   BACKUP_DIR=/data/backups KEEP=14 ./scripts/backup.sh
#
# 恢复示例（详见 docs/deploy.md）：
#   docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T \
#     -e PGPASSWORD="$POSTGRES_PASSWORD" db \
#     pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < backups/backup_XXX.dump

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP="${KEEP:-7}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"

# 从 env 文件读数据库连接变量（已 export 的优先，其次从 .env.prod 提取）。
if [[ -f "$ENV_FILE" ]]; then
  POSTGRES_DB="${POSTGRES_DB:-$(sed -n 's/^POSTGRES_DB=//p' "$ENV_FILE" | tail -1)}"
  POSTGRES_USER="${POSTGRES_USER:-$(sed -n 's/^POSTGRES_USER=//p' "$ENV_FILE" | tail -1)}"
  POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_FILE" | tail -1)}"
fi

: "${POSTGRES_DB:?POSTGRES_DB 未设置}"
: "${POSTGRES_USER:?POSTGRES_USER 未设置}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD 未设置}"

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${BACKUP_DIR}/backup_${STAMP}.dump"
mkdir -p "$BACKUP_DIR"

# 在 db 容器内跑 pg_dump（postgres 镜像自带），-Fc 自定义格式便于 pg_restore。
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T \
  -e PGPASSWORD="$POSTGRES_PASSWORD" \
  db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$OUT"

# 轮转：按时间倒序，只保留最近 KEEP 份，更早的删除。
find "$BACKUP_DIR" -maxdepth 1 -name 'backup_*.dump' -printf '%T@ %p\n' \
  | sort -rn \
  | tail -n +$((KEEP + 1)) \
  | cut -d' ' -f2- \
  | xargs -r rm -f

echo "备份完成：$OUT"
echo "恢复命令：docker compose --env-file $ENV_FILE -f $COMPOSE_FILE exec -T \\"
echo "  -e PGPASSWORD=\"\$POSTGRES_PASSWORD\" db pg_restore -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" --clean --if-exists < $OUT"
