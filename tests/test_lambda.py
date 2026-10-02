import json

from api.app import handler


def test_api_gateway_v2_adapter() -> None:
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/health",
        "rawQueryString": "",
        "headers": {"host": "api.example", "x-forwarded-proto": "https"},
        "requestContext": {
            "http": {"method": "GET", "path": "/health", "sourceIp": "127.0.0.1"},
            "domainName": "api.example",
            "stage": "$default",
        },
        "body": "",
        "isBase64Encoded": False,
    }
    result = handler(event, None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"status": "ok"}
