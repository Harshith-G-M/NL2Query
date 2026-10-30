"""Generate the deterministic sample datasets used by the evaluation set (seeded, reproducible)."""
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "datasets"
random.seed(42)

REGIONS = ["North", "South", "East", "West"]
CATEGORIES = {"Electronics": [("Laptop", 900), ("Phone", 600), ("Headphones", 120)],
              "Furniture": [("Desk", 300), ("Chair", 150), ("Bookshelf", 200)],
              "Office": [("Notebook", 5), ("Pen Set", 12), ("Printer", 250)]}
CHANNELS = ["Online", "Retail", "Partner"]
SEGMENTS = ["Consumer", "SMB", "Enterprise"]
CITIES = ["Bengaluru", "Mumbai", "Delhi", "Chennai", "Pune", "Hyderabad"]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    customers = ["customer_id,name,segment,city,signup_date"]
    for cid in range(1, 81):
        signup = date(2021, 1, 1) + timedelta(days=random.randint(0, 1000))
        customers.append(f"{cid},Customer {cid:02d},{random.choice(SEGMENTS)},{random.choice(CITIES)},{signup}")
    (OUT / "customers.csv").write_text("\n".join(customers) + "\n")

    sales = ["order_id,order_date,region,category,product,quantity,unit_price,discount,revenue,customer_id,channel"]
    start = date(2023, 1, 1)
    for oid in range(1, 601):
        cat = random.choice(list(CATEGORIES))
        product, price = random.choice(CATEGORIES[cat])
        qty = random.randint(1, 6)
        disc = random.choice([0, 0, 0, 0.05, 0.10, 0.15, 0.20])
        d = start + timedelta(days=random.randint(0, 546))  # 2023-01-01 .. 2024-06-30
        rev = round(qty * price * (1 - disc), 2)
        cust = random.randint(1, 70)  # customers 71-80 never order
        sales.append(f"{oid},{d},{random.choice(REGIONS)},{cat},{product},{qty},{price},{disc},{rev},{cust},{random.choice(CHANNELS)}")
    (OUT / "sales.csv").write_text("\n".join(sales) + "\n")
    print("wrote", OUT / "sales.csv", OUT / "customers.csv")


if __name__ == "__main__":
    main()
