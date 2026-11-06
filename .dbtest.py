import asyncio, aiosqlite, os
db = r"C:\Users\zzq\Desktop\新建文件夹\.llmwiki\index.db"
print("exists:", os.path.exists(db), "size:", os.path.getsize(db))
print("dir writable probe:", end=" ")
p = os.path.join(os.path.dirname(db), "probe.txt")
open(p, "w").write("x"); os.remove(p); print("ok")

async def main():
    try:
        c = await aiosqlite.connect(db)
        cur = await c.execute("select count(*) from document_chunks")
        print("read ok, chunks =", (await cur.fetchone())[0])
        await c.execute("insert into document_chunks (id, document_id, chunk_index, content, source_content, token_count) values ('t1','none','0','x','x',1)")
        await c.commit()
        print("write ok")
        await c.execute("delete from document_chunks where id='t1'")
        await c.commit()
        await c.close()
    except Exception as e:
        print("FAILED:", type(e).__name__, e)

asyncio.run(main())
