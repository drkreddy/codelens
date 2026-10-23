# Unsupported calls

These calls cannot be expressed in OpenAPI. They are not HTTP calls with a verb and path (ISO 8583, SOAP without verb or path, JMS, Kafka and similar). They were found by static analysis.

## DCMS

| Protocol | Operation | At | Confidence |
|---|---|---|---|
| ISO 8583 | MTI 0302 | card-adapter/src/main/java/com/bank/cards/infra/DcmsClient.java:27 | certain |

## Finacle (CBS)

| Protocol | Operation | At | Confidence |
|---|---|---|---|
| SOAP (Spring WS) | FinacleGateway.getBalance, URL https://finacle-uat.bank.internal/FIWebService/balance, no verb or path in code, body `<BalInq>{accountId}</BalInq>` | mobile-banking-api/src/main/java/com/bank/mobile/infra/FinacleGateway.java:17 | certain |
