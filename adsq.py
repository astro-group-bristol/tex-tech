#!/usr/bin/python3
# Written by Fergus Baker 2025 Copyleft GPL 3.0

__doc__ = """
Search or fetch BibTeX entries from NASA/ADS.

If the terms provided are `bibcode`, this program will fetch the BibTeX
entries. Everything else will be treated as a search query.

!!! note

    You need to have a NASA/ADS token exported to the `ADS_TOKEN` environment
    variable, if you want to do NASA/ADS things.

    See the NASA/ADS API documentation for how to get one:

        https://ui.adsabs.harvard.edu/help/api/
"""
import argparse
import logging
import json
import dataclasses
import urllib.request
import urllib.parse
import http.client
import re
import os

from enum import Enum

ADS_QUERY_URL = "https://api.adsabs.harvard.edu/v1/search/query"
ADS_EXPORT_BIBTEX_URL = "https://api.adsabs.harvard.edu/v1/export/bibtex"
ADS_TOKEN = os.environ.get("ADS_TOKEN")
ADS_BIBTEX_URL = "https://api.adsabs.harvard.edu/v1/export/bibtex"
MAX_AUTHORS = 4
BIBCODE_PATTERN = r"^[a-zA-Z0-9]+\.+[a-zA-Z0-9]+\.+[a-zA-Z0-9]+$"

logger = logging.getLogger(__name__)


class InvalidQuery(Exception): ...


def _get_auth_header() -> dict[str, str]:
    if not ADS_TOKEN:
        print(
            """
Error: NO ADS TOKEN

Please export an ADS API access token to the `ADS_TOKEN` environment
variable. On most shells this can be done with:

    export ADS_TOKEN="..."

You can get a token (for free) by following the instructions here:

    https://ui.adsabs.harvard.edu/help/api/

DO NOT SHARE YOUR TOKEN WITH ANYONE.
"""
        )
        exit(1)

    return {"Authorization": "Bearer " + ADS_TOKEN}


def _url_escape(s: str) -> str:
    return urllib.parse.quote_plus(s, safe="^")


@dataclasses.dataclass
class ADSQuery:
    terms: None | list[str] = None
    authors: None | list[str] = None
    year: None | str = None
    database: None | str = None

    def is_valid(self) -> bool:
        # year enough is not enough to perform a search
        return self.authors or self.terms

    def format_ads(self) -> str:
        q = []
        if self.terms:
            q.append(_url_escape(" ".join(self.terms)))

        if self.authors:
            for author in self.authors:
                q.append("author:" + _url_escape(author))

        if self.year:
            q.append("year:" + _url_escape(self.year))

        if self.database:
            q.append("database:" + _url_escape(self.database))

        return "&fq=".join(q)


def _canonical_name(author: str) -> str:
    return " ".join(i.strip() for i in reversed(author.split(",")))


def pretty_print_doc(doc: dict):
    _done = set()
    s: list[tuple[str, list[str]]] = []

    if "author" in doc:
        authors = doc["author"]
        formatted_authors = [
            _canonical_name(i) for i in authors[0 : min(len(authors), MAX_AUTHORS)]
        ]

        if len(authors) > MAX_AUTHORS:
            formatted_authors.append("et al.")

        author = "; ".join(formatted_authors)
        s.append(("Author", [author]))
        _done.add("author")

    if "date" in doc:
        date = doc["date"]
        s.append(("Date", [date]))
        _done.add("date")

    if "title" in doc:
        title = "; ".join(doc["title"])
        s.append(("Title", [title]))
        _done.add("title")

    if "bibcode" in doc:
        bibcode = doc["bibcode"]
        s.append(("Bibcode", [bibcode]))
        s.append(("URL", [f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract"]))
        _done.add("bibcode")

    if "links_data" in doc:
        links = []
        for link in doc["links_data"]:
            l = json.loads(link)
            link_type = l["type"]
            url = l["url"]
            access = l["access"]

            suffix = ""
            if access:
                suffix = f"({access})"

            links.append(f"{link_type} {suffix} {url}")

        s.append(("Links", links))
        _done.add("links_data")

    for k, v in doc.items():
        if not k in _done:
            if isinstance(v, list):
                s.append((k, v))
            else:
                s.append((k, [v]))

    padding = max(len(i[0]) for i in s) + 2
    space = " " * padding

    text = ""
    for k, lines in s:
        text += k.rjust(padding) + ": "
        if len(lines) > 1:
            for i, v in enumerate(lines):
                if i == 0:
                    text += "- " + str(v) + "\n"
                else:
                    text += space + "  - " + str(v) + "\n"
        else:
            text += str(lines[0]) + "\n"

    print(text)


def _ads_search_query(query: str, fields: str) -> http.client.HTTPResponse:
    encoded_query = "?q=" + query + "&fl=" + fields

    req_url = ADS_QUERY_URL + encoded_query

    logger.debug("Making query: %s", req_url)

    req = urllib.request.Request(req_url, headers=_get_auth_header())

    return urllib.request.urlopen(req)


def _ads_export_bibcode(bibcode: list[str]) -> str:
    req_url = ADS_EXPORT_BIBTEX_URL

    body = json.dumps({"bibcode": bibcode}).encode()

    logger.debug("Making query: %s", req_url)

    req = urllib.request.Request(req_url, headers=_get_auth_header(), data=body)

    return urllib.request.urlopen(req)


def ads_search(query: str, fields: str) -> str:
    resp = _ads_search_query(query, fields)
    return resp.read().decode()


def ads_export(bibcode: list[str]) -> str:
    resp = _ads_export_bibcode(bibcode)
    return resp.read().decode()


def run_query(query: ADSQuery, fields: str, as_json: bool):
    logger.debug("Query object: %s", query)

    if not query.is_valid():
        raise InvalidQuery("Not enough information for query supplied.")

    q = query.format_ads()
    logger.debug("Formatted query: %s", q)

    data = ads_search(q, fields)

    if as_json:
        print(data)
        return

    d = json.loads(data)
    docs = d["response"]["docs"]

    for doc in sorted(docs, key=lambda x: x.get("citation_count", 0)):
        pretty_print_doc(doc)


def is_bibcode(term: str) -> bool:
    return re.match(BIBCODE_PATTERN, term) is not None


def fetch_bibtex(terms: list[str], as_json: bool):
    logger.debug("Fields: %s", terms)
    data = ads_export(terms)

    if as_json:
        print(data)
        return

    d = json.loads(data)
    print(d["export"].strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="adsq",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-a", "--author", action="append", help="Author name, in standard ADS format."
    )
    parser.add_argument("-y", "--year", default=None)
    parser.add_argument("--loglevel", default="warning", help="Set the logging level.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output fetched JSON.",
    )
    parser.add_argument(
        "--bibcode",
        action="store_true",
        help="Force interpretation of the argument as a bibcode",
    )
    parser.add_argument("terms", nargs="*")
    parser.add_argument(
        "--fields",
        help="Which fields to request",
        default="author,date,pub,title,bibcode,citation_count,links_data",
    )
    parser.add_argument(
        "--database",
        help="Which databse to request from.",
        default="astronomy",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.loglevel.upper())

    if (
        not args.author
        and not args.year
        and args.terms
        and (all(is_bibcode(i) for i in args.terms) or args.bibcode)
    ):
        fetch_bibtex(args.terms, args.json)
    else:
        query = ADSQuery(args.terms, args.author, args.year, args.database)
        run_query(query, args.fields, args.json)
