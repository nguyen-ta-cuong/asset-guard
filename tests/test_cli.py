from asset_guard import main


def test_main_prints_greeting(capsys):
    main()
    captured = capsys.readouterr()
    assert "asset-guard" in captured.out
