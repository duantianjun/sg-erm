# -*- coding: utf-8 -*-
"""naming 包名构造/解析单元测试。"""
import pytest

from app.services.naming import (
    get_package_name,
    get_package_url,
    get_index_url,
    parse_package_name,
    validate_path_segment,
)


@pytest.mark.unit
class TestGetPackageName:
    def test_without_build(self):
        assert get_package_name("postgis", "3.4", "pg", "16.4") == "postgis-3.4-pg16.4"

    def test_with_build(self):
        assert (
            get_package_name("postgis", "3.4", "pg", "16.4", "6.51")
            == "postgis-3.4-pg16.4-build-6.51"
        )


@pytest.mark.unit
class TestParsePackageName:
    def test_parse_postgis_pg16(self):
        result = parse_package_name("postgis-3.4-pg16.4")
        assert result == {
            "name": "postgis",
            "version": "3.4",
            "flavor": "pg",
            "postgres_version": "16.4",
            "build": None,
        }

    def test_parse_with_build(self):
        result = parse_package_name("postgis-3.4-pg16.4-build-6.51")
        assert result["build"] == "6.51"

    def test_extract_package_name_for_whitelist(self):
        # 对应项目硬约束：包名提取按 '-' 切分取首段
        pkg = "postgis-3.4-pg16.4"
        assert pkg.split("-")[0] == "postgis"


@pytest.mark.unit
class TestValidatePathSegment:
    @pytest.mark.parametrize(
        "segment,expected",
        [
            ("com.ongres", True),
            ("x86_64", True),
            ("", False),
            ("..", False),
            ("a/b", False),
            ("a\\b", False),
            ("postgis", True),
        ],
    )
    def test_validation(self, segment, expected):
        assert validate_path_segment(segment) is expected


@pytest.mark.unit
class TestUrlConstruction:
    def test_package_url(self):
        url = get_package_url(
            "https://ext.stackgres.io/repository/",
            "com.ongres",
            "x86_64",
            "linux",
            "postgis-3.4-pg16.4",
        )
        assert url == "https://ext.stackgres.io/repository/com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar"

    def test_index_url(self):
        assert get_index_url("https://ext.stackgres.io/repository") == \
            "https://ext.stackgres.io/repository/v2/index.json"
