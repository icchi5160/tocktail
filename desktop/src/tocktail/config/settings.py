"""Validated, serializable preferences; independent of Qt widgets."""

from dataclasses import asdict, dataclass
import math

from tocktail.browser.protocol import domain_name


@dataclass(frozen=True)
class Settings:
    idle_seconds: float = 300
    save_titles: bool = False
    excluded_apps: tuple[str, ...] = ()
    pinned: bool = False
    always_on_top: bool = False
    excluded_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.idle_seconds) or not 1 <= self.idle_seconds <= 86400:
            raise ValueError("Idle threshold must be between 1 and 86400 seconds.")
        if any(type(value) is not bool for value in (self.save_titles, self.pinned, self.always_on_top)):
            raise ValueError("Preference toggles must be boolean.")
        names = tuple(sorted({name.strip().casefold() for name in self.excluded_apps if name.strip()}))
        if any(not name.endswith(".exe") or any(c in name for c in "/\\\r\n") for name in names):
            raise ValueError("Use executable names such as chrome.exe, one per line.")
        object.__setattr__(self, "excluded_apps", names)
        domains = tuple(sorted({domain_name(name) for name in self.excluded_domains if name.strip()}))
        object.__setattr__(self, "excluded_domains", domains)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def excludes_domain(self, domain: str) -> bool:
        normalized = domain.casefold().rstrip(".")
        return any(normalized == excluded or normalized.endswith("." + excluded) for excluded in self.excluded_domains)

    @classmethod
    def from_dict(cls, value: object, *, idle_seconds: object = None) -> "Settings":
        if not isinstance(value, dict):
            value = {}
        try:
            apps = value.get("excluded_apps", ())
            if not isinstance(apps, (tuple, list)) or not all(isinstance(app, str) for app in apps):
                raise ValueError("Invalid exclusion list")
            domains = value.get("excluded_domains", ())
            if not isinstance(domains, (tuple, list)) or not all(isinstance(domain, str) for domain in domains):
                raise ValueError("Invalid domain exclusion list")
            return cls(
                idle_seconds=float(value.get("idle_seconds", 300) if idle_seconds is None else idle_seconds),
                save_titles=value.get("save_titles", False),
                excluded_apps=tuple(apps), pinned=value.get("pinned", False),
                always_on_top=value.get("always_on_top", False),
                excluded_domains=tuple(domains),
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Saved desktop settings are invalid. Check the settings database.") from error
