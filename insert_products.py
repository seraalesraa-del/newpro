import os
import random
import re
import django

# Force Django to use Render's database
os.environ["DATABASE_URL"] = "postgresql://amazon_db_dkki_user:pHoItJ5cu1bmhAg60CTlk2HlMkJQFWk1@dpg-d7qch3sm0tmc73d1gpr0-a.virginia-postgres.render.com/amazon_db_dkki"  # Replace with your actual Render DB URL
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AmazonProject.settings")
django.setup()

from django.core.files.storage import default_storage
from products.models import Product

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
ROLE_ORDER = ["referee", "referrer"]

def is_image(filename: str) -> bool:
    return filename.lower().endswith(IMAGE_EXTENSIONS)

def extract_sequence(filename: str) -> int:
    match = re.search(r"product\s*\((\d+)\)", filename, re.IGNORECASE)
    return int(match.group(1)) if match else 0

def extract_product_name(filename: str) -> str:
    base_name = os.path.splitext(filename)[0]
    base_name = base_name.replace("_", " ").replace("-", " ").strip()
    base_name = re.sub(r"\s+", " ", base_name)
    return base_name.title() if base_name else "Product"

def product_exists(key: str) -> bool:
    return Product.objects.filter(file=key).exists()

def main():
    print("Connecting to Backblaze and Render database...")

    try:
        # List files from Backblaze
        dirs, files = default_storage.listdir("products")
    except Exception as e:
        print(f"Error listing Backblaze folder: {e}")
        return

    images = [f for f in files if is_image(f)]
    if not images:
        print("No images found in Backblaze products/ folder.")
        return

    print(f"Found {len(images)} images in Backblaze.")

    sequence = 1
    for idx, filename in enumerate(sorted(images, key=lambda n: (extract_sequence(n), n.lower()))):
        key = f"products/{filename}"
        role = ROLE_ORDER[idx % len(ROLE_ORDER)]

        if product_exists(key):
            print(f"✅ Already exists: {filename} (skipped)")
            continue

        try:
            product = Product(
                name=extract_product_name(filename),
                price=round(random.uniform(10, 100), 2),
                is_active=True,
                cycle_number=1,
                role_pool=role,
                sequence_in_cycle=sequence,
            )
            product.file.name = key  # Point to Backblaze
            product.save()
            print(f"✅ Created: {filename} (role: {role}, price: {product.price})")
            sequence += 1
        except Exception as e:
            print(f"❌ Failed to create {filename}: {e}")

    print("Done!")

if __name__ == "__main__":
    main()
