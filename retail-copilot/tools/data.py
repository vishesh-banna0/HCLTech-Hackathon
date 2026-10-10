"""Allowlisted pandas calculations over the supplied CSV package."""
from pathlib import Path
import re
import pandas as pd
from tools.policies import input_dir

ANCHOR = pd.Timestamp("2026-10-10 12:00:00", tz="Asia/Kolkata")
FILES = ("support_cases", "product_master", "inventory", "store_master", "employee_master",
         "receipts", "vendor_master", "returns_quality", "escalation_matrix")


def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def number(value):
    result = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(result) else float(result)


def clean(row):
    return {k: (None if pd.isna(v) else v.item() if hasattr(v, "item") else v)
            for k, v in row.items() if k not in ("issue_text", "employee_name", "employee_id", "root_cause_hint")}


class DataStore:
    def __init__(self):
        self.tables = {}
        self.missing = []
        directory = input_dir("data")
        for name in FILES:
            path = directory / (name + ".csv")
            if path.exists():
                self.tables[name] = pd.read_csv(path, dtype=str, keep_default_na=False)
            else:
                self.missing.append(path.name)

    def table(self, name, columns):
        frame = self.tables.get(name)
        if frame is None:
            return None, [name + ".csv missing"]
        missing = set(columns) - set(frame.columns)
        return (frame, []) if not missing else (None, [name + ".csv missing columns: " + ", ".join(sorted(missing))])

    def resolve(self, query, entities):
        """Verify explicit IDs and exact normalized names; never silently choose multiple."""
        notes = []
        for field, master, id_col, name_col in (("sku", "product_master", "sku", "name"),
                                                 ("store_id", "store_master", "store_id", "store_name")):
            table, errors = self.table(master, [id_col, name_col])
            notes += errors
            if table is None:
                continue
            explicit = re.findall(r"\b(?:SKU\d+|ST\d+)\b", query, re.I)
            explicit = [x.upper() for x in explicit if x.upper().startswith("SKU" if field == "sku" else "ST")]
            if len(set(explicit)) > 1:
                notes.append("Multiple " + field + " values; clarify which one.")
                entities[field] = None
                continue
            if explicit:
                matches = table[table[id_col].str.upper() == explicit[0]]
                if len(matches) == 1:
                    entities[field] = explicit[0]
                else:
                    entities[field] = None
                    notes.append("Unknown " + field + ": " + explicit[0])
                continue
            candidate = entities.get("product_name") if field == "sku" else None
            if not candidate:
                matches = table[table[name_col].map(lambda x: len(norm(x)) >= 8 and norm(x) in norm(query))]
            else:
                matches = table[table[name_col].map(norm) == norm(candidate)]
            if len(matches) == 1:
                entities[field] = str(matches.iloc[0][id_col])
            elif len(matches) > 1:
                entities[field] = None
                notes.append("Several " + name_col + " matches; specify " + field + ".")
            elif entities.get(field):
                entities[field] = None
                notes.append("Could not verify " + field + ".")
        return notes

    def inventory_issue(self, entities):
        sku, store = entities.get("sku"), entities.get("store_id")
        if not sku or not store:
            return {"summary": "Specific inventory lookup requires store ID and SKU.", "missing": [x for x, v in (("store_id", store), ("sku", sku)) if not v], "used": []}
        frame, errors = self.table("inventory", ["store_id", "sku", "on_hand", "system_qty", "reorder_point"])
        if errors:
            return {"summary": "; ".join(errors), "missing": errors, "used": []}
        rows = frame[(frame.store_id == store) & (frame.sku == sku)]
        if len(rows) != 1:
            return {"summary": "Inventory row absent or ambiguous; quantity unknown.", "missing": ["unique inventory row"], "used": ["inventory.csv"]}
        row = clean(rows.iloc[0].to_dict())
        result = {"summary": "Verified store/SKU inventory", "inventory": row,
                  "used": ["inventory.csv"], "missing": []}
        warehouse = frame[(frame.store_id == "WH001") & (frame.sku == sku)]
        if len(warehouse) == 1:
            result["warehouse_inventory"] = clean(warehouse.iloc[0].to_dict())
        on, system, reorder = (number(row.get(k)) for k in ("on_hand", "system_qty", "reorder_point"))
        result["below_reorder"] = on <= reorder if on is not None and reorder is not None else None
        result["discrepancy"] = system - on if system is not None and on is not None else None
        products, _ = self.table("product_master", ["sku", "vendor_id", "high_demand", "name"])
        if products is not None:
            match = products[products.sku == sku]
            if len(match) == 1:
                result["product"] = clean(match.iloc[0].to_dict())
                result["used"].append("product_master.csv")
                entities["vendor_id"] = str(match.iloc[0].vendor_id)
        receipt = self.vendor_shortage(entities)
        result["receipts_and_vendor"] = receipt
        result["used"] = sorted(set(result["used"] + receipt["used"]))
        cases, _ = self.table("support_cases", ["case_id", "store_id", "sku", "status", "category_label"])
        if cases is not None:
            related = cases[(cases.store_id == store) & (cases.sku == sku)]
            result["related_case_count"] = len(related)
            result["related_open_case_count"] = len(related[related.status.isin(["Open", "In Progress"])])
            result["used"].append("support_cases.csv")
        return result

    def vendor_shortage(self, entities):
        frame, errors = self.table("receipts", ["receipt_id", "store_id", "sku", "vendor_id", "po_qty", "received_qty", "receipt_date"])
        if errors:
            return {"summary": "; ".join(errors), "missing": errors, "used": []}
        rows = frame.copy()
        for key in ("store_id", "sku", "vendor_id"):
            if entities.get(key):
                rows = rows[rows[key] == entities[key]]
        dates = pd.to_datetime(rows.receipt_date, errors="coerce")
        rows = rows[dates.notna() & (dates.dt.date <= ANCHOR.date())].copy()
        rows["short_qty"] = (pd.to_numeric(rows.po_qty, errors="coerce") - pd.to_numeric(rows.received_qty, errors="coerce")).clip(lower=0)
        short = rows[rows.short_qty > 0].sort_values("receipt_date", ascending=False)
        result = {"summary": f"{len(short)} observed short receipts; {int(short.short_qty.sum())} units short across matching valid rows.",
                  "short_receipts": [clean(r) for r in short.head(6).to_dict("records")],
                  "by_vendor": short.groupby("vendor_id").agg(receipts=("receipt_id", "size"), short_units=("short_qty", "sum")).reset_index().sort_values("short_units", ascending=False).head(10).to_dict("records"),
                  "used": ["receipts.csv"], "missing": []}
        vendors, _ = self.table("vendor_master", ["vendor_id", "effective_from", "fill_rate", "on_time_rate"])
        if vendors is not None:
            v = vendors.copy()
            if entities.get("vendor_id"):
                v = v[v.vendor_id == entities["vendor_id"]]
            effective = pd.to_datetime(v.effective_from, errors="coerce")
            v = v[effective.notna() & (effective.dt.date <= ANCHOR.date())].sort_values("effective_from", ascending=False)
            if entities.get("vendor_id") and len(v):
                result["vendor_record"] = clean(v.iloc[0].to_dict())
                result["used"].append("vendor_master.csv")
            elif len(v) and len(short):
                latest = v.drop_duplicates("vendor_id")
                result["vendor_performance"] = [clean(r) for r in latest[latest.vendor_id.isin(short.vendor_id)][["vendor_id", "fill_rate", "on_time_rate", "effective_from"]].head(10).to_dict("records")]
                result["used"].append("vendor_master.csv")
        return result

    def open_high_priority(self, entities):
        frame, errors = self.table("support_cases", ["case_id", "created_ts", "store_id", "category_label", "priority_label", "status"])
        if errors:
            return {"summary": "; ".join(errors), "missing": errors, "used": []}
        dates = pd.to_datetime(frame.created_ts, errors="coerce").dt.tz_localize("Asia/Kolkata")
        window = str(entities.get("time_window") or "last 24 hours").lower()
        start = ANCHOR - pd.Timedelta(days=30 if "30" in window else 1)
        rows = frame[dates.notna() & (dates > start) & (dates <= ANCHOR)
                     & frame.priority_label.isin(["P1", "P2"]) & frame.status.isin(["Open", "In Progress"])]
        for key in ("store_id", "region"):
            if entities.get(key) and key == "store_id":
                rows = rows[rows.store_id == entities[key]]
        used = ["support_cases.csv"]
        if entities.get("region"):
            stores, err = self.table("store_master", ["store_id", "region"])
            if not err and stores.store_id.is_unique:
                rows = rows.merge(stores[["store_id", "region"]], on="store_id", validate="many_to_one")
                rows = rows[rows.region.str.casefold() == entities["region"].casefold()]
                used.append("store_master.csv")
        grouped = rows.groupby(["store_id", "category_label", "status"], dropna=False).size().reset_index(name="count")
        return {"summary": f"{len(rows)} unresolved P1/P2 cases from ({start}, {ANCHOR}] IST; future records excluded.",
                "groups": grouped.head(30).to_dict("records"), "used": used, "missing": [],
                "invalid_timestamp_count": int(dates.isna().sum())}

    def quality_trend(self, entities, query=""):
        frame, errors = self.table("returns_quality", ["return_id", "created_ts", "store_id", "sku", "reason"])
        if errors:
            return {"summary": "; ".join(errors), "missing": errors, "used": []}
        dates = pd.to_datetime(frame.created_ts, errors="coerce").dt.tz_localize("Asia/Kolkata")
        rows = frame[dates.notna() & (dates > ANCHOR - pd.Timedelta(days=30)) & (dates <= ANCHOR)]
        if "football" in query.lower():
            rows = rows[rows.reason.str.contains("football", case=False, na=False)]
        for key in ("store_id", "sku"):
            if entities.get(key):
                rows = rows[rows[key] == entities[key]]
        groups = rows.groupby(["sku", "store_id", "reason"]).size().reset_index(name="count").sort_values("count", ascending=False)
        result = {"summary": f"{len(rows)} matching quality returns in the last 30 days.",
                  "groups": groups.head(20).to_dict("records"), "used": ["returns_quality.csv"], "missing": []}
        products, _ = self.table("product_master", ["sku", "vendor_id"])
        if products is not None and products.sku.is_unique:
            joined = rows.merge(products[["sku", "vendor_id"]], on="sku", validate="many_to_one")
            result["by_product_vendor"] = joined.groupby("vendor_id").size().reset_index(name="quality_returns").sort_values("quality_returns", ascending=False).head(10).to_dict("records")
            result["used"].append("product_master.csv")
            vendors, _ = self.table("vendor_master", ["vendor_id", "effective_from", "fill_rate", "on_time_rate"])
            if vendors is not None:
                effective = pd.to_datetime(vendors.effective_from, errors="coerce")
                latest = vendors[effective.notna() & (effective.dt.date <= ANCHOR.date())].sort_values("effective_from", ascending=False).drop_duplicates("vendor_id")
                result["vendor_performance"] = [clean(r) for r in latest[latest.vendor_id.isin(joined.vendor_id)][["vendor_id", "fill_rate", "on_time_rate", "effective_from"]].head(10).to_dict("records")]
                result["used"].append("vendor_master.csv")
        cases, _ = self.table("support_cases", ["case_id", "created_ts", "sku", "category_label"])
        if cases is not None:
            case_dates = pd.to_datetime(cases.created_ts, errors="coerce").dt.tz_localize("Asia/Kolkata")
            relevant = cases[case_dates.notna() & (case_dates > ANCHOR - pd.Timedelta(days=30)) & (case_dates <= ANCHOR) & (cases.category_label == "Product Quality and Safety")]
            if entities.get("sku"):
                relevant = relevant[relevant.sku == entities["sku"]]
            elif "football" in query.lower():
                relevant = relevant[relevant.issue_text.str.contains("football", case=False, na=False)] if "issue_text" in relevant.columns else relevant.iloc[0:0]
            result["corroborating_quality_cases"] = len(relevant)
            result["used"].append("support_cases.csv")
        return result

    def route(self, category, priority, trigger):
        frame, errors = self.table("escalation_matrix", ["category", "trigger", "priority", "team", "channel", "sla_minutes", "human_approval_required"])
        if errors:
            return {"team": "unconfirmed", "reason": "; ".join(errors), "used": []}
        rows = frame[(frame.category == category) & (frame.priority == priority)]
        matches = rows[rows.trigger == trigger] if trigger else rows.iloc[0:0]
        if len(matches) == 1:
            return dict(clean(matches.iloc[0].to_dict()), used=["escalation_matrix.csv"])
        if len(rows) == 1:
            return dict(clean(rows.iloc[0].to_dict()), used=["escalation_matrix.csv"])
        return {"team": "unconfirmed", "reason": "No unique specific route; refer to Store Manager.", "used": ["escalation_matrix.csv"]}
