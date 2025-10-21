if __name__ == "__main__":
    from app.main import app, settings
    import uvicorn

    uvicorn.run(
            app,
            host=settings.server.host,
            port=settings.server.port,
            log_level=settings.server.log_level,
            reload=False  # 手动禁用 reload
        )
