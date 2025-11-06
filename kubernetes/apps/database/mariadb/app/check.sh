#!/bin/bash -ec

password_aux="${MYSQL_ROOT_PASSWORD:-}"
if [[ -f "${MYSQL_ROOT_PASSWORD_FILE:-}" ]]; then
    password_aux=$(cat "$MYSQL_ROOT_PASSWORD_FILE")
fi
# Connect via TCP (localhost:3306) instead of socket
mariadb-admin status -uroot -p"${password_aux}" -h127.0.0.1 -P3306 2>/dev/null || mysqladmin status -uroot -p"${password_aux}" -h127.0.0.1 -P3306 2>/dev/null || mariadb-admin ping -uroot -p"${password_aux}" -h127.0.0.1 -P3306 2>/dev/null || mysqladmin ping -uroot -p"${password_aux}" -h127.0.0.1 -P3306
