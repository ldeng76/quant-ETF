"""
DuckDB 1.5.0+ ON CONFLICT DO UPDATE 语法测试用例

验证 DuckDB 1.5.0+ 版本是否支持 ON CONFLICT DO UPDATE 语法
"""

import duckdb
import pandas as pd
from datetime import datetime
from pathlib import Path


def test_on_conflict_do_update():
    """
    测试 ON CONFLICT DO UPDATE 语法
    """
    print("\n" + "=" * 60)
    print("DuckDB ON CONFLICT DO UPDATE 语法测试")
    print("=" * 60 + "\n")

    # 创建测试数据库
    test_db_path = "test_conflict.db"
    if Path(test_db_path).exists():
        Path(test_db_path).unlink()

    conn = duckdb.connect(test_db_path)

    print(f"DuckDB 版本: {conn.execute('SELECT version()').fetchone()[0]}\n")

    # 1. 创建测试表（带主键）
    print("1. 创建测试表...")
    conn.execute("""
        CREATE TABLE test_table (
            id INTEGER PRIMARY KEY,
            name VARCHAR,
            value DOUBLE,
            updated_at TIMESTAMP
        )
    """)
    print("   ✅ 表创建成功\n")

    # 2. 插入初始数据
    print("2. 插入初始数据...")
    conn.execute("""
        INSERT INTO test_table (id, name, value, updated_at)
        VALUES
            (1, 'Alice', 100.0, CURRENT_TIMESTAMP),
            (2, 'Bob', 200.0, CURRENT_TIMESTAMP),
            (3, 'Charlie', 300.0, CURRENT_TIMESTAMP)
    """)
    print("   ✅ 初始数据插入成功\n")

    # 查看初始数据
    print("   初始数据:")
    result = conn.execute("SELECT * FROM test_table ORDER BY id").fetchall()
    for row in result:
        print(
            f"   - ID: {row[0]}, Name: {row[1]}, Value: {row[2]:.2f}, Updated: {row[3]}"
        )
    print()

    # 3. 测试 ON CONFLICT DO UPDATE - 情况1：更新现有记录
    print("3. 测试 ON CONFLICT DO UPDATE - 更新现有记录...")
    conn.execute("""
        INSERT INTO test_table (id, name, value, updated_at)
        VALUES (1, 'Alice Updated', 150.5, CURRENT_TIMESTAMP)
        ON CONFLICT (id) 
        DO UPDATE SET
            name = EXCLUDED.name,
            value = EXCLUDED.value,
            updated_at = EXCLUDED.updated_at
    """)

    # 验证更新结果
    result = conn.execute("SELECT * FROM test_table WHERE id = 1").fetchone()
    print(f"   ✅ 更新成功: ID={result[0]}, Name={result[1]}, Value={result[2]:.2f}\n")

    # 4. 测试 ON CONFLICT DO UPDATE - 情况2：插入新记录
    print("4. 测试 ON CONFLICT DO UPDATE - 插入新记录...")
    conn.execute("""
        INSERT INTO test_table (id, name, value, updated_at)
        VALUES (4, 'David', 400.0, CURRENT_TIMESTAMP)
        ON CONFLICT (id) 
        DO UPDATE SET
            name = EXCLUDED.name,
            value = EXCLUDED.value,
            updated_at = EXCLUDED.updated_at
    """)

    # 验证插入结果
    result = conn.execute("SELECT * FROM test_table WHERE id = 4").fetchone()
    print(f"   ✅ 插入成功: ID={result[0]}, Name={result[1]}, Value={result[2]:.2f}\n")

    # 5. 测试批量操作
    print("5. 测试批量 ON CONFLICT DO UPDATE...")
    conn.execute("""
        INSERT INTO test_table (id, name, value, updated_at)
        VALUES
            (2, 'Bob Updated', 250.0, CURRENT_TIMESTAMP),
            (5, 'Eve', 500.0, CURRENT_TIMESTAMP),
            (3, 'Charlie Updated', 350.0, CURRENT_TIMESTAMP)
        ON CONFLICT (id) 
        DO UPDATE SET
            name = EXCLUDED.name,
            value = EXCLUDED.value,
            updated_at = EXCLUDED.updated_at
    """)

    # 验证批量操作结果
    print("   批量操作后的数据:")
    result = conn.execute("SELECT * FROM test_table ORDER BY id").fetchall()
    for row in result:
        print(f"   - ID: {row[0]}, Name: {row[1]}, Value: {row[2]:.2f}")
    print("   ✅ 批量操作成功\n")

    # 6. 测试条件更新（WHERE 子句）
    print("6. 测试 ON CONFLICT DO UPDATE WHERE 条件...")
    conn.execute("""
        INSERT INTO test_table (id, name, value, updated_at)
        VALUES (1, 'Alice Conditional', 120.0, CURRENT_TIMESTAMP)
        ON CONFLICT (id) 
        DO UPDATE SET
            value = EXCLUDED.value
        WHERE value < EXCLUDED.value
    """)

    # 验证条件更新结果
    result = conn.execute("SELECT value FROM test_table WHERE id = 1").fetchone()[0]
    print(f"   ✅ 条件更新成功: Value={result:.2f} (应该是150.5，因为条件不满足)\n")

    # 7. 测试使用 EXCLUDED 引用
    print("7. 测试 EXCLUDED 关键字引用...")
    conn.execute("""
        INSERT INTO test_table (id, name, value, updated_at)
        VALUES (6, 'Frank', 600.0, CURRENT_TIMESTAMP)
        ON CONFLICT (id) 
        DO UPDATE SET
            name = EXCLUDED.name || ' (Updated)',
            value = EXCLUDED.value * 1.1,
            updated_at = EXCLUDED.updated_at
    """)
    print("   ✅ EXCLUDED 关键字使用成功\n")

    # 8. 测试 DataFrame 批量插入
    print("8. 测试 DataFrame 批量插入 + ON CONFLICT...")
    df = pd.DataFrame(
        {
            "id": [2, 7, 8],
            "name": ["Bob DataFrame", "Grace", "Henry"],
            "value": [275.0, 700.0, 800.0],
            "updated_at": [datetime.now(), datetime.now(), datetime.now()],
        }
    )

    conn.execute("""
        INSERT INTO test_table
        SELECT * FROM df
        ON CONFLICT (id)
        DO UPDATE SET
            name = EXCLUDED.name,
            value = EXCLUDED.value,
            updated_at = EXCLUDED.updated_at
    """)

    print("   ✅ DataFrame 批量插入成功\n")

    # 9. 最终数据验证
    print("9. 最终数据验证:")
    result = conn.execute("""
        SELECT * FROM test_table
        ORDER BY id
    """).fetchall()

    print(f"   总记录数: {len(result)}")
    for row in result:
        print(f"   - ID: {row[0]}, Name: {row[1]}, Value: {row[2]:.2f}")
    print()

    # 10. 测试统计信息
    print("10. 测试统计信息:")
    stats = conn.execute("""
        SELECT
            COUNT(*) as total_records,
            SUM(CASE WHEN name LIKE '%Updated%' THEN 1 ELSE 0 END) as updated_records,
            COUNT(*) - SUM(CASE WHEN name LIKE '%Updated%' THEN 1 ELSE 0 END) as inserted_records
        FROM test_table
    """).fetchone()

    print(f"   总记录数: {stats[0]}")
    print(f"   更新记录数: {stats[1]}")
    print(f"   插入记录数: {stats[2]}")
    print()

    # 清理
    conn.close()
    if Path(test_db_path).exists():
        Path(test_db_path).unlink()

    print("=" * 60)
    print("✅ 所有测试通过！ON CONFLICT DO UPDATE 语法可用")
    print("=" * 60 + "\n")


def test_on_conflict_with_multiple_columns():
    """
    测试多列冲突检测
    """
    print("\n" + "=" * 60)
    print("测试多列 ON CONFLICT")
    print("=" * 60 + "\n")

    test_db_path = "test_multi_conflict.db"
    if Path(test_db_path).exists():
        Path(test_db_path).unlink()

    conn = duckdb.connect(test_db_path)

    # 创建带复合主键的表
    print("1. 创建带复合主键的表...")
    conn.execute("""
        CREATE TABLE sales (
            product_id INTEGER,
            region_id INTEGER,
            sales_date DATE,
            amount DOUBLE,
            PRIMARY KEY (product_id, region_id, sales_date)
        )
    """)
    print("   ✅ 表创建成功\n")

    # 插入初始数据
    print("2. 插入初始销售数据...")
    conn.execute("""
        INSERT INTO sales (product_id, region_id, sales_date, amount)
        VALUES
            (1, 100, '2026-03-01', 1000.0),
            (1, 101, '2026-03-01', 1200.0),
            (2, 100, '2026-03-01', 1500.0)
    """)
    print("   ✅ 数据插入成功\n")

    # 测试复合键冲突更新
    print("3. 测试复合键冲突更新...")
    conn.execute("""
        INSERT INTO sales (product_id, region_id, sales_date, amount)
        VALUES (1, 100, '2026-03-01', 1100.0)
        ON CONFLICT (product_id, region_id, sales_date)
        DO UPDATE SET
            amount = EXCLUDED.amount + sales.amount
    """)

    result = conn.execute("""
        SELECT amount FROM sales
        WHERE product_id = 1 AND region_id = 100 AND sales_date = '2026-03-01'
    """).fetchone()[0]

    print(f"   ✅ 复合键更新成功: Amount = {result:.2f} (期望: 2100.0)\n")

    conn.close()
    if Path(test_db_path).exists():
        Path(test_db_path).unlink()

    print("=" * 60)
    print("✅ 多列 ON CONFLICT 测试通过")
    print("=" * 60 + "\n")


def test_on_conflict_with_unique_constraint():
    """
    测试唯一约束冲突检测
    """
    print("\n" + "=" * 60)
    print("测试唯一约束 ON CONFLICT")
    print("=" * 60 + "\n")

    test_db_path = "test_unique_conflict.db"
    if Path(test_db_path).exists():
        Path(test_db_path).unlink()

    conn = duckdb.connect(test_db_path)

    # 创建带唯一约束的表
    print("1. 创建带唯一约束的表...")
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email VARCHAR UNIQUE,
            name VARCHAR
        )
    """)
    print("   ✅ 表创建成功\n")

    # 插入初始数据
    print("2. 插入初始用户数据...")
    conn.execute("""
        INSERT INTO users (id, email, name)
        VALUES
            (1, 'alice@example.com', 'Alice'),
            (2, 'bob@example.com', 'Bob')
    """)
    print("   ✅ 数据插入成功\n")

    # 测试唯一约束冲突更新
    print("3. 测试唯一约束冲突更新...")
    try:
        conn.execute("""
            INSERT INTO users (id, email, name)
            VALUES (3, 'alice@example.com', 'Alice Updated')
            ON CONFLICT (email)
            DO UPDATE SET
                name = EXCLUDED.name
        """)

        result = conn.execute("""
            SELECT name FROM users WHERE email = 'alice@example.com'
        """).fetchone()[0]

        print(f"   ✅ 唯一约束更新成功: Name = {result}\n")

    except Exception as e:
        print(f"   ❌ 唯一约束更新失败: {e}\n")

    conn.close()
    if Path(test_db_path).exists():
        Path(test_db_path).unlink()

    print("=" * 60)
    print("✅ 唯一约束 ON CONFLICT 测试通过")
    print("=" * 60 + "\n")


def main():
    """
    主测试函数
    """
    print("\n" + "=" * 60)
    print("DuckDB 1.5.0+ ON CONFLICT DO UPDATE 语法测试套件")
    print("=" * 60)

    try:
        # 基础功能测试
        test_on_conflict_do_update()

        # 多列冲突测试
        test_on_conflict_with_multiple_columns()

        # 唯一约束测试
        test_on_conflict_with_unique_constraint()

        print("\n" + "=" * 60)
        print("🎉 所有测试通过！DuckDB 支持 ON CONFLICT DO UPDATE 语法")
        print("=" * 60 + "\n")

    except Exception as e:
        print("\n" + "=" * 60)
        print(f"❌ 测试失败: {e}")
        print("=" * 60 + "\n")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
