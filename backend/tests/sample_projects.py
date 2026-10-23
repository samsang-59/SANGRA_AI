"""Small sample projects for the Docker test runner tests (B7) and later the agent tests."""

from pathlib import Path

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>shop</groupId>
  <artifactId>shop</artifactId>
  <version>1.0</version>
  <properties>
    <maven.compiler.release>17</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.10.2</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.2.5</version>
      </plugin>
    </plugins>
  </build>
</project>
"""

DISCOUNT = """package shop;

public class Discount {
    public static double summerRate() {
        return %s;
    }
}
"""

DISCOUNT_TEST = """package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import org.junit.jupiter.api.Test;

class DiscountTest {
    @Test
    void summerSaleIsTenPercent() {
        assertEquals(0.10, Discount.summerRate(), 1e-9);
    }
}
"""


def java_project(folder: Path, rate: str = "0.10", broken: bool = False) -> Path:
    """The Amazon discount example. rate "0.05" makes the test fail; broken=True does not compile."""
    (folder / "src/main/java/shop").mkdir(parents=True)
    (folder / "src/test/java/shop").mkdir(parents=True)
    (folder / "pom.xml").write_text(POM)
    body = DISCOUNT % rate
    if broken:
        body = body.replace("return", "retrun")
    (folder / "src/main/java/shop/Discount.java").write_text(body)
    (folder / "src/test/java/shop/DiscountTest.java").write_text(DISCOUNT_TEST)
    return folder


def python_project(folder: Path, rate: str = "0.10", extra_test: str = "") -> Path:
    folder.mkdir(parents=True)
    (folder / "discount.py").write_text(f"def summer_rate():\n    return {rate}\n")
    (folder / "test_discount.py").write_text(
        "from discount import summer_rate\n\n\ndef test_summer_sale_is_ten_percent():\n"
        "    assert summer_rate() == 0.10\n" + extra_test)
    return folder


JAVA_COMMAND = "mvn -q -B test"
PYTHON_COMMAND = "pip install -q pytest && python -m pytest -q"
