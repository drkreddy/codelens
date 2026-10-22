"""Known external integrations, keyed by what shows up in source code.

Each entry maps a qualified import/type prefix to a (service_kind, label).
The linker turns matches into EXTERNAL_SERVICE nodes such as
"service:http-client".
"""

from __future__ import annotations

PYTHON_INTEGRATIONS: dict[str, tuple[str, str]] = {
    "requests": ("http-client", "HTTP client (requests)"),
    "httpx": ("http-client", "HTTP client (httpx)"),
    "aiohttp": ("http-client", "HTTP client (aiohttp)"),
    "urllib.request": ("http-client", "HTTP client (urllib)"),
    "grpc": ("rpc", "gRPC"),
    "boto3": ("cloud", "AWS SDK (boto3)"),
    "google.cloud": ("cloud", "Google Cloud SDK"),
    "sqlalchemy": ("database", "SQL database (SQLAlchemy)"),
    "psycopg2": ("database", "PostgreSQL (psycopg2)"),
    "psycopg": ("database", "PostgreSQL (psycopg)"),
    "sqlite3": ("database", "SQLite"),
    "pymongo": ("database", "MongoDB"),
    "redis": ("cache", "Redis"),
    "kafka": ("messaging", "Kafka"),
    "confluent_kafka": ("messaging", "Kafka"),
    "pika": ("messaging", "RabbitMQ"),
    "celery": ("messaging", "Celery"),
    "smtplib": ("email", "SMTP"),
}

JAVA_INTEGRATIONS: dict[str, tuple[str, str]] = {
    "org.springframework.web.client.RestTemplate": ("http-client", "HTTP client (RestTemplate)"),
    "org.springframework.web.client.RestClient": ("http-client", "HTTP client (RestClient)"),
    "org.springframework.web.reactive.function.client.WebClient": ("http-client", "HTTP client (WebClient)"),
    "java.net.http.HttpClient": ("http-client", "HTTP client (java.net.http)"),
    "okhttp3": ("http-client", "HTTP client (OkHttp)"),
    "org.apache.http": ("http-client", "HTTP client (Apache)"),
    "org.springframework.cloud.openfeign": ("http-client", "HTTP client (Feign)"),
    "io.grpc": ("rpc", "gRPC"),
    "org.springframework.jdbc.core.JdbcTemplate": ("database", "SQL database (JdbcTemplate)"),
    "org.springframework.data.jpa": ("database", "SQL database (Spring Data JPA)"),
    "jakarta.persistence": ("database", "SQL database (JPA)"),
    "javax.persistence": ("database", "SQL database (JPA)"),
    "java.sql": ("database", "SQL database (JDBC)"),
    "org.springframework.data.mongodb": ("database", "MongoDB"),
    "org.springframework.data.redis": ("cache", "Redis"),
    "org.springframework.kafka": ("messaging", "Kafka"),
    "org.apache.kafka": ("messaging", "Kafka"),
    "org.springframework.amqp": ("messaging", "RabbitMQ"),
    "software.amazon.awssdk": ("cloud", "AWS SDK"),
    "com.amazonaws": ("cloud", "AWS SDK"),
    "org.springframework.mail": ("email", "SMTP"),
}


def match(catalog: dict[str, tuple[str, str]], qualified: str) -> tuple[str, str] | None:
    """Longest-prefix match on dotted boundaries."""
    best: str | None = None
    for prefix in catalog:
        if qualified == prefix or qualified.startswith(prefix + "."):
            if best is None or len(prefix) > len(best):
                best = prefix
    return catalog[best] if best else None


# Client types whose method calls leave the process: qualified prefix -> (protocol, client).
JAVA_CLIENTS: dict[str, tuple[str, str]] = {
    "org.springframework.web.client.RestTemplate": ("HTTP", "RestTemplate"),
    "org.springframework.web.client.RestClient": ("HTTP", "RestClient"),
    "org.springframework.web.reactive.function.client.WebClient": ("HTTP", "WebClient"),
    "java.net.http.HttpClient": ("HTTP", "java.net.http"),
    "okhttp3.OkHttpClient": ("HTTP", "OkHttp"),
    "org.apache.hc.client5": ("HTTP", "Apache HttpClient"),
    "org.apache.http.client": ("HTTP", "Apache HttpClient"),
    "org.springframework.ws.client.core.WebServiceTemplate": ("SOAP", "Spring WS"),
    "jakarta.xml.ws": ("SOAP", "JAX-WS"),
    "javax.xml.ws": ("SOAP", "JAX-WS"),
    "org.springframework.jms.core.JmsTemplate": ("JMS/MQ", "JmsTemplate"),
    "com.ibm.mq": ("JMS/MQ", "IBM MQ"),
    "org.springframework.kafka.core.KafkaTemplate": ("Kafka", "KafkaTemplate"),
    "org.springframework.amqp.rabbit.core.RabbitTemplate": ("AMQP", "RabbitTemplate"),
    "org.jpos.iso": ("ISO 8583", "jPOS"),
    "org.jpos.q2.iso": ("ISO 8583", "jPOS"),
}

# Only these operations count as outbound for clients that also have local helpers.
CLIENT_OPERATIONS: dict[str, set[str]] = {
    "jPOS": {"send", "request"},
}
