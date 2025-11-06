#!/bin/bash -ec

password_aux="${MYSQL_ROOT_PASSWORD:-}"
if [[ -f "${MYSQL_ROOT_PASSWORD_FILE:-}" ]]; then
    password_aux=$(cat "$MYSQL_ROOT_PASSWORD_FILE")
fi
# Use mariadb-admin instead of mysqladmin (deprecated)
mariadb-admin status -uroot -p"${password_aux}" || mysqladmin status -uroot -p"${password_aux}"
