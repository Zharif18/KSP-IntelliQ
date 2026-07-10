# functions/ksp_backend/main.py
from zcatalyst import initialize

def get_current_officer(request, response):
    catalyst_app = initialize(request)
    user = catalyst_app.userManagement().get_current_user()
    zuid = user.get('zuid')

    query = f"SELECT * FROM Officers WHERE zuid='{zuid}'"
    result = catalyst_app.zcql().execute_query(query)

    if not result:
        response.set_status(404)
        response.send({"error": "Officer record not found"})
        return

    response.set_status(200)
    response.send({"officer": result[0]})