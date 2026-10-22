from jobscanner.emailer import pretty_location


def test_pretty_location():
    assert pretty_location("Multi Location\nLehi, UT, United States\nSan Jose, CA, United States") == "Lehi, UT / San Jose, CA"
    assert pretty_location("Savannah, GA, United States") == "Savannah, GA"
    assert pretty_location("United States") == "United States"
    assert pretty_location("") == ""
