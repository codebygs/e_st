import sys
import json
import re
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import NamedTuple, Optional, Dict, Any
from enum import Enum
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from dataclasses import dataclass

BASE_HOST = "https://www.e-st.lv"
LOGIN_URL = f"{BASE_HOST}/lv/private/user-authentification/"
ACCOUNT_URL = f"{BASE_HOST}/lv/private/klienta-informacija/"
COUNTER_URL = f"{BASE_HOST}/lv/private/skara/counters/smart"
DATA_URL = f"{BASE_HOST}/lv/private/paterini-un-norekini/paterinu-grafiki/"

PERIOD_DAY = "D"
PERIOD_MONTH = "M"
PERIOD_YEAR = "Y"

GRANULARITY_NATIVE = "N"
GRANULARITY_HOUR = "H"
GRANULARITY_DAY = "D"

class Direction(Enum):
    CONSUMED = "consumed"
    RETURNED = "returned"

class DataPoint(NamedTuple):
    timestamp: int
    value: float

Statistics = Dict[Direction, list[DataPoint]]

class ApiException(Exception):
    pass

class ApiAuthException(Exception):
    pass

@dataclass
class Customer:
    full_name: str
    eic_code: str

@dataclass
class Counter:
    id: str
    address: str

class Api:
    def __init__(self, login: str, password: str):
        self.login = login
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/97.0.4692.71 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": BASE_HOST,
        })
        self.session.max_redirects = 3
        self.session.verify = True

    def _get_stats_url(self, options: Dict[str, Any]) -> str:
        date = datetime.now(ZoneInfo("Europe/Riga")) - timedelta(days=1)
        counter_id = options.get("counter_id")
        period = options.get("period") or PERIOD_DAY
        year = options.get("year") or date.year
        month = options.get("month") or date.month
        day = options.get("day") or date.day
        granularity = options.get("granularity") or GRANULARITY_HOUR
        params = {
            "counterNumber": counter_id,
            "period": period
        }

        if period == PERIOD_YEAR:
            params["year"] = year
        elif period == PERIOD_MONTH:
            params.update({
                "year": year,
                "month": month,
                "granularity": granularity
            })
        elif period == PERIOD_DAY:
            params.update({
                "date": f"{day:02d}.{month:02d}.{year}",
                "granularity": granularity
            })

        return f"{DATA_URL}?{urlencode(params)}"

    def _format_stats_response(self, data: Dict[str, Any]) -> Statistics:
        formatted = {}
        for key, direction in {"A+": Direction.CONSUMED.value, "A-": Direction.RETURNED.value}.items():
            formatted[direction] = [
                DataPoint(item["timestamp"] / 1000, item["value"])
                for item in data.get("values", {}).get(key, {}).get("total", {}).get("data", [])
            ]
        return formatted

    def _fetch_stats(self, options: Dict[str, Any]) -> Dict[Any, Any]:
        url = self._get_stats_url(options)
        content = self._fetch(url)
        soup = BeautifulSoup(content, "lxml")
        chart_div = soup.select_one("div.chart")

        if not chart_div or not chart_div.has_attr("data-values"):
            raise ApiException("Failed extracting chart data.")

        decoded = json.loads(chart_div["data-values"])

        return self._format_stats_response(decoded)

    def _fetch(self, url: str) -> Dict[Any, Any]:
        try:
            response = self.session.get(url, timeout=30)

            response.raise_for_status()

            content = response.text
            soup = BeautifulSoup(content, "lxml")

            # Check if login form exists
            if soup.select_one("form.authenticate"):
                fields = ["_token", "returnUrl"]
                values = {field: soup.select_one(f"input[name={field}]")["value"] for field in fields}
                values.update({"login": self.login, "password": self.password})
                login_response = self.session.post(LOGIN_URL, data=urlencode(values))
                login_response.raise_for_status()
                content = login_response.text
                soup = BeautifulSoup(content, "lxml")

                if soup.select_one("form.authenticate"):
                    raise ApiAuthException("Error connecting to api. Invalid e-mail or password.")
                    
        except requests.RequestException as e:
            raise ApiException(f"Failed fetching data from {url}: {repr(e)}") from e
        except (KeyError, ValueError, TypeError) as e:
            raise ApiException(f"Failed decoding or extracting data: {repr(e)}") from e

        return content


    # Public API
    def authenticate(self) -> Customer:
        content = self._fetch(ACCOUNT_URL)
        soup = BeautifulSoup(content, "lxml")
        details = soup.select_one("div.customerDetails")

        if not details:
            raise ApiException("Customer details not found.")
        
        full_name = details.find("h2").get_text(strip=True) if details.find("h2") else None
        eic_code = details.find("p").get_text(strip=True).split()[-1] if details.find("p") else None

        return Customer(full_name, eic_code)
    
    def get_counters(self) -> list[Counter]:
        content = self._fetch(COUNTER_URL)
        soup = BeautifulSoup(content, "lxml")
        counter_rows = soup.select("tr.counter")

        if not counter_rows:
            raise ApiException("No counters found.")

        counters = []

        for row in counter_rows:
            if not row.has_attr("data-filter-string"):
                raise ApiException("Failed extracting counter data.")

            counter_string = row["data-filter-string"]
            
            match = re.match(r"^(.*)\s+(\d+)\s+(\d+)$", counter_string)

            if not match:
                raise ApiException(f"String format not recognized: {counter_string}")

            address, _, counter_id = match.groups()

            counters.append(Counter(id=counter_id.strip(), address=address.strip()))

        return counters

    def get_day_data(self, counter_id: str, year: Optional[int] = None, month: Optional[int] = None, day: Optional[int] = None, granularity: str = GRANULARITY_NATIVE):
        return self._fetch_stats({
            "counter_id": counter_id,
            "period": PERIOD_DAY,
            "year": year,
            "month": month,
            "day": day,
            "granularity": granularity,
        })

    def get_month_data(self, counter_id: str, year: Optional[int] = None, month: Optional[int] = None, granularity: str = GRANULARITY_DAY):
        return self._fetch_stats({
            "counter_id": counter_id,
            "period": PERIOD_MONTH,
            "year": year,
            "month": month,
            "granularity": granularity,
        })

    def get_year_data(self, counter_id: str, year: Optional[int] = None):
        return self._fetch_stats({
            "counter_id": counter_id,
            "period": PERIOD_YEAR,
            "year": year,
        })

    def get_start_timestamp(self, counter_id: str) -> Optional[int]:
        date = datetime.now(ZoneInfo("Europe/Riga"))
        url = self._get_stats_url({
            "counter_id": counter_id,
            "period": PERIOD_DAY,
            "year": date.year,
            "month": date.month,
            "day": date.day,
            "granularity": GRANULARITY_HOUR,
        })
        content = self._fetch(url)
        soup = BeautifulSoup(content, "lxml")
        date_input = soup.select_one("input#date")

        if not date_input or not date_input.has_attr("data-min-date"):
            return None
        
        return (datetime
            .strptime(date_input["data-min-date"], "%Y-%m-%d")
            .replace(tzinfo=ZoneInfo("Europe/Riga")).timestamp())