import os

import psycopg2


def get_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "5")),
    )
