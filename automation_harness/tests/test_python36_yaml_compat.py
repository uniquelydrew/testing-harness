import yaml

from automation_harness.compat import python36


def test_yaml_compat_strips_unsupported_sort_keys(monkeypatch):
    calls = []

    def legacy_safe_dump(data, stream=None, **kwargs):
        if "sort_keys" in kwargs:
            raise TypeError("dump_all() got an unexpected keyword argument 'sort_keys'")
        calls.append(dict(kwargs))
        return "ok"

    monkeypatch.setattr(yaml, "safe_dump", legacy_safe_dump)

    python36._install_yaml_compat()

    assert yaml.safe_dump({}, sort_keys=False, allow_unicode=True) == "ok"
    assert calls[-1] == {"allow_unicode": True}
