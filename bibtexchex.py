#!python3
# Written by Fergus Baker 2025 Copyleft GPL 3.0

__doc__ = """
This script takes a LaTeX file and a BibTeX file as input with the aim of
outputting a nicely formatted BibTeX file with the following properties:

- All items in the BibTeX file should be cited in the LaTeX (no superfluous
  citations)

- All entries in the BibTeX file should be in the NASA/ADS format.

It achieves this in the following way. First it uses regex expressions to parse
the input files. It then creates sets based on the citation label and finds the
intersection between those cited and those in the citation file.

For each needed citation, the script then tries to extract some information
that unambiguously identifies the item, be it DOI, arXiv identifier, or
similar. It will not use title or author information, as that is generally
ambiguous in a large database search. It will use this unambiguous identifier
to fetch the ADS Bibcode from NASA/ADS, and use all the collected bibcodes to
export a library of citations, also from NASA/ADS.

This is then written to one of the output files.

The other output file contains those entries that either:

- Could not be unambiguously identified.

- Did not unambiguously resolve in NASA/ADS.

!!! note

    This script will not warn you about which citations you have cited but do
    not appear in the LaTeX file (i.e. undefined entries). The LaTeX compiler
    already does that for you. It will tell you how many you are missing
    though, as a compromise.

!!! note

    You need to have a NASA/ADS token exported to the `ADS_TOKEN` environment
    variable, if you want to do NASA/ADS things.

    See the NASA/ADS API documentation for how to get one:

        https://ui.adsabs.harvard.edu/help/api/
"""
import argparse
import logging
import re
import pathlib
import json
import dataclasses
import urllib.request
import urllib.parse
import http.client
import itertools
import os
import time

from enum import Enum

CITE_REGEX = r"\\cite(t|p|alp|alt)?(\[([^\]]*)\])*\{(?P<citation>[^\}]+)\}"
BIB_REGEX = r"@\w+\{(?P<citation>[^\},]+)"
ADS_QUERY_URL = "https://api.adsabs.harvard.edu/v1/search/query"
ADS_TOKEN = os.environ.get("ADS_TOKEN")
ADS_BIBTEX_URL = "https://api.adsabs.harvard.edu/v1/export/bibtex"
QUERY_SLEEP = 0.2

logger = logging.getLogger(__name__)


class AmbiguousBibNodeError(Exception): ...


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


def ads_get_bibtex(bibcodes: list[str]) -> http.client.HTTPResponse:
    payload = {"bibcode": bibcodes, "sort": "first_author asc"}
    req = urllib.request.Request(
        ADS_BIBTEX_URL, headers=_get_auth_header(), data=json.dumps(payload).encode()
    )
    return urllib.request.urlopen(req)


def ads_make_bib(bibcodes: list[str]) -> str:
    resp = ads_get_bibtex(bibcodes)
    return json.loads(resp.read().decode())["export"]


def ads_search_query(data: dict[str, str]) -> http.client.HTTPResponse:
    query = " ".join(k + ":" + v for (k, v) in data.items())
    encoded_query = "?" + urllib.parse.urlencode({"q": query, "fl": "bibcode,title"})

    req_url = ADS_QUERY_URL + encoded_query
    req = urllib.request.Request(req_url, headers=_get_auth_header())

    return urllib.request.urlopen(req)


def ads_search_bibcode(data: dict[str, str]) -> str:
    resp = ads_search_query(data)
    data = json.loads(resp.read().decode())
    bibcodes = [i["bibcode"] for i in data["response"]["docs"]]
    return bibcodes[0]


class NodeType(Enum):
    CITATION = 0
    BIBENTRY = 0


@dataclasses.dataclass
class Node:
    start: int
    end: int
    node_type: NodeType
    value: str

    def __hash__(self) -> str:
        return hash(self.value)

    def __eq__(self, other) -> str:
        return self.value.__eq__(other.value)


def tex_all_citations(contents: str) -> list[Node]:
    nodes = []

    for item in re.finditer(CITE_REGEX, contents, re.MULTILINE):
        for k in item.group("citation").split(sep=","):
            nodes.append(Node(item.start(), item.end(), NodeType.CITATION, k.strip()))

    return nodes


def bib_all_citations(contents: str) -> list[Node]:
    nodes = []

    items = re.finditer(BIB_REGEX, contents, re.MULTILINE)
    ends = re.finditer(r"^\s*}\s*$", contents, re.MULTILINE)

    for item, end in zip(items, ends):
        nodes.append(
            Node(item.start(), end.end(), NodeType.BIBENTRY, item.group("citation"))
        )

    return nodes


def check_cits(tex_cits: set[Node], bib_cits: set[Node]):
    return [i for i in bib_cits if i in tex_cits], tex_cits.difference(bib_cits)


def bib_node_is_ads(bib: str, node: Node) -> bool:
    details = bib[node.start : node.end]
    return "adsurl =" in details


def findfirst(expr: re.Pattern, text: str) -> None | re.Match:
    matches = [i for i in re.finditer(expr, text)]
    if matches:
        return matches[0]
    return None


def bib_extract_query(bib: str, node: Node) -> dict[str, str]:
    """
    Tries to extract information that could be used to unambiguously resolve
    the item in NASA/ADS.

    Attempts to find the following, in order:
    - DOI
    - ADS Bibcode
    - arXiv identifier

    Will raise an `AmbiguousBibNodeError` if no data could be found.
    """
    details = bib[node.start : node.end]

    fields = re.finditer(r"\b(doi|note|url|journal)\s*=\s*\{(?P<info>[^\}]+)}", details)

    query = {}
    for i in fields:
        _type = i.group(1)

        if _type == "doi":
            query["doi"] = i.group("info")

        elif _type == "note" or _type == "journal":
            note = i.group("info")

            arxiv = findfirst(r"arXiv:\s?(?P<identifier>\d+\.\d+)", note)
            if arxiv:
                query["arXiv"] = arxiv.group("identifier")

            bibcode = findfirst(r"ADS Bibcode:\s?(?P<identifier>[^ ]+)", note)
            if bibcode:
                query["bibcode"] = bibcode.group("identifier")

        elif _type == "url":
            url = i.group("info")

            arxiv = findfirst(r"arxiv\.org\/(abs|pdf)\/(?P<identifier>[^ \/]+)", url)
            if arxiv:
                query["arXiv"] = arxiv.group("identifier")

            bibcode = findfirst(
                r"adsabs\.harvard\.edu\/abs\/(?P<identifier>[^ \/]+)", url
            )
            if bibcode:
                query["bibcode"] = bibcode.group("identifier")

        else:
            raise "Unreachable"

    if len(query) == 0:
        raise AmbiguousBibNodeError("Could not extract query information")

    return {k: urllib.parse.unquote(v) for (k, v) in query.items()}


def main_entry(
    latex_file: str,
    bibtex_file: str,
    fetch_ads=True,
    outpath="output.bib",
    missing_outpath="missing.bib",
):
    contents = pathlib.Path(latex_file).read_text()
    bib_contents = pathlib.Path(bibtex_file).read_text()

    tex_cits = set(tex_all_citations(contents))
    bib_cits = set(bib_all_citations(bib_contents))

    needed, missing = check_cits(tex_cits, bib_cits)
    ads_needed = [i for i in needed if not bib_node_is_ads(bib_contents, i)]

    failed: list[Node] = []
    bibcodes: list[str] = []
    queries: list[tuple[int, dict[str, str]]] = []

    for i, item in enumerate(ads_needed):
        try:
            query = bib_extract_query(bib_contents, item)
            if "bibcode" in query:
                bibcodes.append(query["bibcode"])
            else:
                queries.append((i, query))
        except AmbiguousBibNodeError:
            failed.append(item)

    print("Parsing summary:")
    print(f" Unique citations  : {len(tex_cits)}")
    print(f" BibTeX entries    : {len(bib_cits)}")
    print(f" . needed entries  : {len(needed)}")
    print(f" . missing entries : {len(missing)}")
    print(f" Missing ADS url   : {len(ads_needed)}")
    print(f" . has bibcode     : {len(bibcodes)}")
    print(f" . has query       : {len(queries)}")
    print(f" . no query        : {len(failed)}")
    print()

    if not fetch_ads:
        text = sorted([bib_contents[i.start : i.end] for i in needed])
        with open(outpath, "w") as f:
            f.write("\n".join(text))

        print(f"Written '{outpath}'")

    else:
        print("Fetching from NASA/ADS")
        errors = []
        for i, (index, q) in enumerate(queries):
            try:
                bibcode = ads_search_bibcode(q)
                bibcodes.append(bibcode)
            except Exception as e:
                errors.append((index, q, e))

            print(f"Done {i + 1} of {len(queries)} (errors = {len(errors)})", end="\r")
            time.sleep(QUERY_SLEEP)

        if errors:
            print("ERRORS:")
            for err in errors:
                print(err)

        missing_bib = [
            bib_contents[i.start : i.end]
            for i in itertools.chain(failed, (ads_needed[i[0]] for i in errors))
        ]

        # fetch all of the bibcodes
        print(bibcodes)
        new_bib = ads_make_bib(bibcodes)
        with open(outpath, "w") as f:
            f.write(new_bib)

        print(f"Written '{outpath}'")

        with open(missing_outpath, "w") as f:
            f.write("\n".join(missing_bib))

        print(f"Written '{missing_outpath}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="bibtexchex",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("latexfile")
    parser.add_argument("bibtexfile")
    parser.add_argument("--loglevel", default="warning", help="Set the logging level.")
    parser.add_argument(
        "-o", "--outfile", default="output.bib", help="Output filepath."
    )
    parser.add_argument(
        "-m",
        "--missing-file",
        default="missing.bib",
        help="Output filepath for those bib entries that could not be resolved in NASA/ADS.",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Do network requests to fetch missing data from NASA/ADS.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.loglevel.upper())

    main_entry(
        args.latexfile,
        args.bibtexfile,
        fetch_ads=args.fetch,
        outpath=args.outfile,
        missing_outpath=args.missing_file,
    )
