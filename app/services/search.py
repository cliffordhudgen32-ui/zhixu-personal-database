"""SQLite-specific FTS adapter. Short Chinese terms use substring matching."""
from sqlalchemy import and_, or_, text
from app.database import engine
from app.models import DailyEntry

def keyword_condition(words, scope='all'):
    terms = words.split()[:20]
    columns = [DailyEntry.title] if scope == 'title' else [DailyEntry.content, DailyEntry.normalized_content] if scope == 'content' else [DailyEntry.title, DailyEntry.content, DailyEntry.summary, DailyEntry.normalized_content]
    conditions = []
    for term in terms:
        if len(term) >= 3 and engine.dialect.name == 'sqlite':
            prefix = 'title : ' if scope == 'title' else '{content normalized_content} : ' if scope == 'content' else ''
            # One parameter per clause; literal terms cannot inject FTS syntax.
            match = prefix + '"' + term.replace('"', '""') + '"'
            from sqlalchemy import bindparam, select, column, table
            fts = table('entries_fts', column('rowid'), column('entries_fts'))
            conditions.append(DailyEntry.id.in_(select(fts.c.rowid).where(fts.c.entries_fts.op('MATCH')(bindparam(None, match, unique=True)))))
        else:
            conditions.append(or_(*(col.contains(term, autoescape=True) for col in columns)))
    return and_(*conditions)

def create_fts(connection):
    connection.exec_driver_sql("CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(title, content, summary, content='entries', content_rowid='id', tokenize='trigram')")
    connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
      INSERT INTO entries_fts(rowid,title,content,summary) VALUES(new.id,new.title,new.content,new.summary); END""")
    connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
      INSERT INTO entries_fts(entries_fts,rowid,title,content,summary) VALUES('delete',old.id,old.title,old.content,old.summary); END""")
    connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
      INSERT INTO entries_fts(entries_fts,rowid,title,content,summary) VALUES('delete',old.id,old.title,old.content,old.summary);
      INSERT INTO entries_fts(rowid,title,content,summary) VALUES(new.id,new.title,new.content,new.summary); END""")
    connection.exec_driver_sql("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
