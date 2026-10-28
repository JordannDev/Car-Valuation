Google Sheets: [Link](https://docs.google.com/spreadsheets/d/1a2xzOuBIk6wryYi-TIKoL8RtwrjpaPzJmgKg8Jd5f-s/edit?usp=sharing)

# Data Structure

#### Vehicle Information
- VIN (appears strict; no formatting issues - should add a basic VIN validator regex)
- Year (int; YYYY)
- Make
- Model
- Trim (should account for trim in value calc)

#### Dealer Information
- Dealer Name
- Dealer Street
- Dealer City
- Dealer State
- Dealer Zip

#### Usage & Pricing
- Listing Price (can be empty, raw int no comma, decimals, etc should assume full value)
- Listing Mileage (int raw value)
- Used (bool)
- Certified (bool - certified should impact price how?)

#### Misc Vehicle Information
- Style (String - some commonalities, should try and group based on make/model)
- Driven Wheels (should try and match common types; should impact price)
- Engine (should try and match common types; should impact price)
- Fuel Type (empty; Gasoline; Diesel; Biodisel)
- Exterior Color (Empty; N/a are valid options)
- Seller Website (link/url)
- First Seen Date
- Last Seen Date
- Dealer VDP Last Seen Date
- Listing Status (empty/in_transit)
