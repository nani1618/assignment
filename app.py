from flask import Flask, render_template, request, redirect, url_for, session
from datetime import datetime, timedelta
import mysql.connector
import connect
from pathlib import Path

app = Flask(__name__)
app.secret_key = 'COMP636 S2'
app.debug = True

start_date = datetime(2024, 10, 29)
pasture_growth_rate = 65
stock_consumption_rate = 14

db_conn = None

def get_cur():
    global db_conn

    if db_conn is None or not db_conn.is_connected():
        db_conn = mysql.connector.connect(
            user=connect.dbuser,
            password=connect.dbpass,
            host=connect.dbhost,
            database=connect.dbname,
            autocommit=True
        )

    cur = db_conn.cursor(dictionary=True, buffered=False)
    return cur

def get_date():
    cur = get_cur()
    sql = "SELECT curr_date FROM curr_date;"
    cur.execute(sql)

    date = cur.fetchone()['curr_date']
    return date

@app.before_request
def load_date():
    session['curr_date'] = get_date()

@app.route('/')
def home():
    date = str(session['curr_date'])
    return render_template("home.html")

@app.route("/reset")
def reset():
    folder = Path(__file__).parent.resolve()
    with open(folder / 'fms-reset.sql', 'r') as file:
        sql_script = file.read()
        cur = get_cur()
        for sql in sql_script.strip().split(";"):
            if sql.strip():
                cur.execute(sql)
    session['curr_date'] = get_date()
    return redirect(url_for('paddocks'))

@app.route("/mobs")
def mobs():
    cur = get_cur()
    sql = """
        SELECT mobs.id, mobs.name, paddocks.name AS paddock_name
        FROM mobs
        LEFT JOIN paddocks ON mobs.paddock_id = paddocks.id
        ORDER BY mobs.name;
    """
    cur.execute(sql)
    data = cur.fetchall()
    return render_template("mobs.html", mobs=data)

@app.route("/paddocks")
def paddocks():
    cur = get_cur()
    sql = """
        SELECT
            paddocks.id,
            paddocks.name,
            paddocks.area,
            paddocks.dm_per_ha,
            paddocks.total_dm,
            mobs.name AS mob_name,
            COUNT(stock.id) AS stock_count
        FROM paddocks
        LEFT JOIN mobs ON paddocks.id = mobs.paddock_id
        LEFT JOIN stock ON mobs.id = stock.mob_id
        GROUP BY paddocks.id
        ORDER BY paddocks.name;
    """
    cur.execute(sql)
    data = cur.fetchall()
    return render_template("paddocks.html", paddocks=data)

@app.route("/stock")
def stock():
    cur = get_cur()
    sql = """
        SELECT
            mobs.id AS mob_id,
            mobs.name AS mob_name,
            paddocks.name AS paddock_name,
            COUNT(stock.id) AS stock_count,
            AVG(stock.weight) AS average_weight
        FROM mobs
        LEFT JOIN paddocks ON mobs.paddock_id = paddocks.id
        LEFT JOIN stock ON mobs.id = stock.mob_id
        GROUP BY mobs.id
        ORDER BY mobs.name;
    """
    cur.execute(sql)
    data = cur.fetchall()

    for mob in data:
        cur.execute("""
            SELECT
                stock.id AS animal_id,
                stock.dob
            FROM stock
            WHERE stock.mob_id = %s
            ORDER BY stock.id;
        """, (mob['mob_id'],))
        animals = cur.fetchall()
        for animal in animals:
            dob = animal['dob']
            date = session['curr_date']
            age_days = (date - dob).days
            animal['age'] = age_days / 365.25
        mob['animals'] = animals

    return render_template("stock.html", mobs=data)

@app.route("/move_mob/<int:mob_id>", methods=['GET', 'POST'])
def move_mob(mob_id):
    cur = get_cur()

    cur.execute("SELECT * FROM mobs WHERE id = %s;", (mob_id,))
    mob = cur.fetchone()

    if request.method == 'POST':
        new_paddock_id = request.form['paddock_id']
        cur.execute("""
            UPDATE mobs SET paddock_id = %s WHERE id = %s;
        """, (new_paddock_id, mob_id))
        return redirect(url_for('mobs'))

    cur.execute("""
        SELECT paddocks.id, paddocks.name
        FROM paddocks
        WHERE paddocks.id NOT IN (SELECT paddock_id FROM mobs WHERE paddock_id IS NOT NULL AND id != %s)
        OR paddocks.id = %s
        ORDER BY paddocks.name;
    """, (mob_id, mob['paddock_id']))
    paddocks = cur.fetchall()

    return render_template("move_mob.html", mob=mob, available_paddocks=paddocks)

@app.route("/advance_date", methods=['POST'])
def advance_date():
    cur = get_cur()
    cur.execute("UPDATE curr_date SET curr_date = curr_date + INTERVAL 1 DAY;")
    session['curr_date'] = get_date()

    cur.execute("SELECT * FROM paddocks;")
    paddocks = cur.fetchall()

    for paddock in paddocks:
        p_id = paddock['id']
        area = paddock['area']
        dm_per_ha = paddock['dm_per_ha']
        total_dm = paddock['total_dm']

        growth = area * pasture_growth_rate

        cur.execute("SELECT * FROM mobs WHERE paddock_id = %s;", (p_id,))
        mob = cur.fetchone()

        if mob:
            cur.execute("SELECT COUNT(*) AS stock_count FROM stock WHERE mob_id = %s;", (mob['id'],))
            stock_count = cur.fetchone()['stock_count']
            consumption = stock_count * stock_consumption_rate
        else:
            consumption = 0

        new_total_dm = total_dm + growth - consumption
        new_dm_per_ha = new_total_dm / area if area > 0 else 0

        cur.execute("""
            UPDATE paddocks SET total_dm = %s, dm_per_ha = %s WHERE id = %s;
        """, (new_total_dm, new_dm_per_ha, p_id))

    return redirect(url_for('paddocks'))

@app.route("/edit_paddock", methods=['GET', 'POST'])
@app.route("/edit_paddock/<int:paddock_id>", methods=['GET', 'POST'])
def edit_paddock(paddock_id=None):
    cur = get_cur()
    error = None

    if request.method == 'POST':
        name = request.form['name']
        try:
            area = float(request.form['area'])
            dm_per_ha = float(request.form['dm_per_ha'])
            total_dm = area * dm_per_ha
        except ValueError:
            error = "Area and DM/ha must be numeric values."
            paddock = {
                'id': paddock_id,
                'name': name,
                'area': request.form['area'],
                'dm_per_ha': request.form['dm_per_ha'],
                'total_dm': None
            }
            return render_template('edit_paddock.html', paddock=paddock, error=error)

        if paddock_id:
            cur.execute("""
                UPDATE paddocks SET name = %s, area = %s, dm_per_ha = %s, total_dm = %s WHERE id = %s;
            """, (name, area, dm_per_ha, total_dm, paddock_id))
        else:
            cur.execute("""
                INSERT INTO paddocks (name, area, dm_per_ha, total_dm)
                VALUES (%s, %s, %s, %s);
            """, (name, area, dm_per_ha, total_dm))
        return redirect(url_for('paddocks'))
    else:
        if paddock_id:
            cur.execute("SELECT * FROM paddocks WHERE id = %s;", (paddock_id,))
            paddock = cur.fetchone()
            if not paddock:
                return redirect(url_for('paddocks'))
        else:
            paddock = {
                'id': None,
                'name': '',
                'area': '',
                'dm_per_ha': '',
                'total_dm': ''
            }
        return render_template('edit_paddock.html', paddock=paddock)

@app.route("/delete_paddock/<int:paddock_id>")
def delete_paddock(paddock_id):
    cur = get_cur()
    cur.execute("SELECT * FROM paddocks WHERE id = %s;", (paddock_id,))
    paddock = cur.fetchone()
    if paddock:
        cur.execute("UPDATE mobs SET paddock_id = NULL WHERE paddock_id = %s;", (paddock_id,))
        cur.execute("DELETE FROM paddocks WHERE id = %s;", (paddock_id,))
    return redirect(url_for('paddocks'))
