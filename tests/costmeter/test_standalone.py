from pathlib import Path


def test_costmeter_imports_nothing_from_kilnworks():
    package = Path(__file__).parents[2] / "src" / "kilnworks" / "costmeter"
    assert package.is_dir()
    for path in package.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import kilnworks", "from kilnworks")):
                assert "kilnworks.costmeter" in stripped, f"{path.name}: {stripped}"
