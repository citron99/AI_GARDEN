import httpx

from app.services.weather_service import WeatherService, WeatherServiceError


def test_open_meteo_forecast_warnings_and_cache():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if "geocoding-api" in request.url.host:
            return httpx.Response(200, json={"results": [{
                "name": "Rīga",
                "country_code": "LV",
                "latitude": 56.95,
                "longitude": 24.1,
                "timezone": "Europe/Riga",
            }]})
        return httpx.Response(200, json={
            "timezone": "Europe/Riga",
            "daily": {
                "time": ["2026-07-12", "2026-07-13"],
                "weather_code": [3, 65],
                "temperature_2m_max": [31, 12],
                "temperature_2m_min": [12, -1],
                "precipitation_sum": [0, 42],
                "precipitation_probability_max": [5, 95],
                "snowfall_sum": [0, 0],
                "wind_speed_10m_max": [22, 54],
                "wind_gusts_10m_max": [34, 82],
                "et0_fao_evapotranspiration": [5.2, 0.8],
            },
        })

    service = WeatherService(httpx.Client(transport=httpx.MockTransport(handler)))
    forecast = service.get_forecast("Riga", "ru")
    assert forecast.location == "Rīga"
    assert len(forecast.daily) == 2
    assert {warning.kind for warning in forecast.warnings} == {
        "heat", "irrigation_check", "frost", "heavy_rain", "strong_wind",
    }
    assert next(item for item in forecast.warnings if item.kind == "frost").severity == "critical"

    cached = service.get_forecast(" riga ", "ru")
    assert cached == forecast
    assert len(requests) == 2


def test_forecast_falls_back_to_secondary_host():
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if "geocoding-api" in request.url.host:
            return httpx.Response(200, json={"results": [{
                "name": "Riga",
                "latitude": 56.95,
                "longitude": 24.1,
            }]})
        if request.url.host == "api.open-meteo.com":
            raise httpx.ConnectTimeout("timed out")
        return httpx.Response(200, json={
            "timezone": "Europe/Riga",
            "daily": {
                "time": ["2026-07-12"],
                "temperature_2m_max": [20],
                "temperature_2m_min": [10],
            },
        })

    service = WeatherService(httpx.Client(transport=httpx.MockTransport(handler)))
    forecast = service.get_forecast("Riga")
    assert len(forecast.daily) == 1
    assert hosts == [
        "geocoding-api.open-meteo.com",
        "api.open-meteo.com",
        "historical-forecast-api.open-meteo.com",
    ]


def test_weather_service_handles_unknown_location():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []}))
    service = WeatherService(httpx.Client(transport=transport))
    try:
        service.get_forecast("Unknown place")
    except WeatherServiceError as exc:
        assert "не найден" in str(exc)
    else:
        raise AssertionError("Неизвестное место было принято")