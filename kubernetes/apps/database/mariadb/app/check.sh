#!/bin/bash -ec

password_aux="${MYSQL_ROOT_PASSWORD:-}"
if [[ -f "${MYSQL_ROOT_PASSWORD_FILE:-}" ]]; then
    password_aux=$(cat "$MYSQL_ROOT_PASSWORD_FILE")
fi

# Try socket connection first (faster), then TCP as fallback
if [[ -S /run/mysqld/mysqld.sock ]]; then
    mariadb-admin ping -uroot -p"${password_aux}" 2>/dev/null || mysqladmin ping -uroot -p"${password_aux}" 2>/dev/null || exit 1
else
    mariadb-admin ping -uroot -p"${password_aux}" -h127.0.0.1 -P3306 2>/dev/null || mysqladmin ping -uroot -p"${password_aux}" -h127.0.0.1 -P3306 2>/dev/null || exit 1
fi
