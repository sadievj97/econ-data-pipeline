# Review notes for uzb_kaz_sectoral_2000_2023.yaml

Drafted by claude-opus-5-5 on 2026-10-12. Definitions are copied from the source catalogs.

## va_agr

Agriculture, forestry, and fishing, value added (constant 2015 US$)

Chosen:
- **wdi NV.AGR.TOTL.KD** (preferred): Agriculture, forestry, and fishing, value added (constant 2015 US$)
  - Unit: constant 2015 US$ (constant, base year 2015)
  - Definition: Agriculture, forestry, and fishing corresponds to ISIC (Rev. 4) divisions 01-03 and includes the exploitation of vegetal and animal natural resources, comprising the activities of growing of crops, raising and breeding of animals, harvesting of timber and other plants, animals or animal products from a farm or their natural habitats.Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment is 2015. This indicator is expressed in United States dollars.

Alternatives to consider:
- **wdi NV.AGR.TOTL.KN**: Agriculture, forestry, and fishing, value added (constant LCU). Why: Constant local currency; follows national accounts base without USD conversion.
  - Unit: constant LCU (constant)
  - Definition: Agriculture, forestry, and fishing corresponds to ISIC (Rev. 4) divisions 01-03 and includes the exploitation of vegetal and animal natural resources, comprising the activities of growing of crops, raising and breeding of animals, harvesting of timber and other plants, animals or animal products from a farm or their natural habitats.Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment varies by country. This series is expressed in local currency units.

## va_ind

Industry (including construction), value added (constant 2015 US$)

Chosen:
- **wdi NV.IND.TOTL.KD** (preferred): Industry (including construction), value added (constant 2015 US$)
  - Unit: constant 2015 US$ (constant, base year 2015)
  - Definition: Industry (including construction) corresponds to ISIC (Rev.4) divisions 05-43. It is comprised of mining, manufacturing, construction, electricity, water, and gas industries. Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment is 2015. This indicator is expressed in United States dollars.

Alternatives to consider:
- **wdi NV.IND.TOTL.KN**: Industry (including construction), value added (constant LCU). Why: Constant local currency; follows national accounts base without USD conversion.
  - Unit: constant LCU (constant)
  - Definition: Industry (including construction) corresponds to ISIC (Rev.4) divisions 05-43. It is comprised of mining, manufacturing, construction, electricity, water, and gas industries. Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment varies by country. This series is expressed in local currency units.

## va_ser

Services, value added (constant 2015 US$)

Chosen:
- **wdi NV.SRV.TOTL.KD** (preferred): Services, value added (constant 2015 US$)
  - Unit: constant 2015 US$ (constant, base year 2015)
  - Definition: Services industries correspond to ISIC (Rev. 4) divisions 45-99 and includes wholesale and retail trade, repair of motor vehicles, hotels and restaurants, transport, storage and communication, financial intermediation, real estate, renting and business activities, public administration and defense, compulsory social security, education, health and social work, other community, social and personal service activities, private households with employed persons, and extra-territorial organizations and bodies. Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment is 2015. This indicator is expressed in United States dollars.

Alternatives to consider:
- **wdi NV.SRV.TOTL.KN**: Services, value added (constant LCU). Why: Constant local currency; follows national accounts base without USD conversion.
  - Unit: constant LCU (constant)
  - Definition: Services industries correspond to ISIC (Rev. 4) divisions 45-99 and includes wholesale and retail trade, repair of motor vehicles, hotels and restaurants, transport, storage and communication, financial intermediation, real estate, renting and business activities, public administration and defence, compulsory social security, education, health and social work, other community, social and personal service activities, private households with employed persons, and extra-territorial organizations and bodies. Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment varies by country. This series is expressed in local currency units.
- **wdi NV.SRV.TOTL.KD.ZG**: Services, value added (annual % growth). Why: Annual real growth rate if only growth is needed.
  - Unit: annual % growth (not_applicable)
  - Definition: Services industries correspond to ISIC (Rev. 4) divisions 45-99 and includes wholesale and retail trade, repair of motor vehicles, hotels and restaurants, transport, storage and communication, financial intermediation, real estate, renting and business activities, public administration and defence, compulsory social security, education, health and social work, other community, social and personal service activities, private households with employed persons, and extra-territorial organizations and bodies. Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. This indicator denotes the percentage change over each previous year of the constant price (base year 2015) series in United States dollars.

## emp_agr_pct

Employment in agriculture (% of total employment) (modeled ILO estimate)

Chosen:
- **wdi SL.AGR.EMPL.ZS** (preferred): Employment in agriculture (% of total employment) (modeled ILO estimate)
  - Unit: % of total employment (not_applicable)
  - Definition: Employment is defined as persons of working age who were engaged in any activity to produce goods or provide services for pay or profit, whether at work during the reference period or not at work due to temporary absence from a job, or to working-time arrangement. The agriculture sector consists of activities in agriculture, hunting, forestry and fishing, in accordance with division 1 (ISIC 2) or categories A-B (ISIC 3) or category A (ISIC 4).

Alternatives to consider:
- **wdi NV.AGR.EMPL.KD**: Agriculture, forestry, and fishing, value added per worker (constant 2015 US$). Why: Value added per worker in agriculture, if labour productivity is the end goal.
  - Unit: constant 2015 US$ (constant, base year 2015)
  - Definition: Agriculture, forestry, and fishing corresponds to ISIC (Rev. 4) divisions 01-03 and includes the exploitation of vegetal and animal natural resources, comprising the activities of growing of crops, raising and breeding of animals, harvesting of timber and other plants, animals or animal products from a farm or their natural habitats.Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment is 2015. This indicator is expressed in United States dollars.

## emp_ind_pct

Employment in industry (% of total employment) (modeled ILO estimate)

Chosen:
- **wdi SL.IND.EMPL.ZS** (preferred): Employment in industry (% of total employment) (modeled ILO estimate)
  - Unit: % of total employment (not_applicable)
  - Definition: Employment is defined as persons of working age who were engaged in any activity to produce goods or provide services for pay or profit, whether at work during the reference period or not at work due to temporary absence from a job, or to working-time arrangement. The industry sector consists of mining and quarrying, manufacturing, construction, and public utilities (electricity, gas, and water), in accordance with divisions 2-5 (ISIC 2) or categories C-F (ISIC 3) or categories B-F (ISIC 4).

Alternatives to consider:
- **wdi NV.IND.EMPL.KD**: Industry (including construction), value added per worker (constant 2015 US$). Why: Value added per worker in industry, if labour productivity is the end goal.
  - Unit: constant 2015 US$ (constant, base year 2015)
  - Definition: Industry (including construction) corresponds to ISIC (Rev.4) divisions 05-43. It is comprised of mining, manufacturing, construction, electricity, water, and gas industries. Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. The core indicator has been divided by the number of workers in this sector to derive a measure of labor productivity. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment is 2015. This indicator is expressed in United States dollars.

## emp_ser_pct

Employment in services (% of total employment) (modeled ILO estimate)

Chosen:
- **wdi SL.SRV.EMPL.ZS** (preferred): Employment in services (% of total employment) (modeled ILO estimate)
  - Unit: % of total employment (not_applicable)
  - Definition: Employment is defined as persons of working age who were engaged in any activity to produce goods or provide services for pay or profit, whether at work during the reference period or not at work due to temporary absence from a job, or to working-time arrangement. The services sector consists of wholesale and retail trade and restaurants and hotels; transport, storage, and communications; financing, insurance, real estate, and business services; and community, social, and personal services, in accordance with divisions 6-9 (ISIC 2) or categories G-Q (ISIC 3) or categories G-U (ISIC 4).

Alternatives to consider:
- **wdi NV.SRV.EMPL.KD**: Services, value added per worker (constant 2015 US$). Why: Value added per worker in services, if labour productivity is the end goal.
  - Unit: constant 2015 US$ (constant, base year 2015)
  - Definition: Services industries correspond to ISIC (Rev. 4) divisions 45-99 and includes wholesale and retail trade, repair of motor vehicles, hotels and restaurants, transport, storage and communication, financial intermediation, real estate, renting and business activities, public administration and defence, compulsory social security, education, health and social work, other community, social and personal service activities, private households with employed persons, and extra-territorial organizations and bodies. Value added is the contribution to the economy by a producer or an industry or an institutional sector, which is estimated by the total value of output produced and deducting the total value of intermediate consumption of goods and services used to produce that output. The core indicator has been divided by the number of workers in the economy to derive a measure of labor productivity. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment is 2015. This indicator is expressed in United States dollars.

## emp_total

Number of persons engaged (in millions)

Chosen:
- **pwt emp** (preferred): Number of persons engaged (in millions)
  - Unit: millions (not_applicable)
  - Definition: Number of persons engaged (in millions)

## gfcf

Gross fixed capital formation (constant 2015 US$)

Chosen:
- **wdi NE.GDI.FTOT.KD** (preferred): Gross fixed capital formation (constant 2015 US$)
  - Unit: constant 2015 US$ (constant, base year 2015)
  - Definition: Gross fixed capital formation includes acquisitions less disposals of fixed assets during the accounting period, including certain specified expenditures on services that add to the value of non-produced assets. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment is 2015. This indicator is expressed in United States dollars.

Alternatives to consider:
- **wdi NE.GDI.FTOT.KN**: Gross fixed capital formation (constant LCU). Why: Constant local currency version.
  - Unit: constant LCU (constant)
  - Definition: Gross fixed capital formation includes acquisitions less disposals of fixed assets during the accounting period, including certain specified expenditures on services that add to the value of non-produced assets. This indicator is expressed in constant prices, meaning the series has been adjusted to account for price changes over time. The reference year for this adjustment varies by country. This series is expressed in local currency units.
- **wdi NE.GDI.FTOT.ZS**: Gross fixed capital formation (% of GDP). Why: Investment rate as percent of GDP.
  - Unit: % of GDP (not_applicable)
  - Definition: Gross fixed capital formation includes acquisitions less disposals of fixed assets during the accounting period, including certain specified expenditures on services that add to the value of non-produced assets. This indicator is expressed as a percentage of Gross Domestic Product (GDP) which is the total income earned through the production of goods and services in an economic territory during an accounting period.
- **wdi NE.GDI.FTOT.CD**: Gross fixed capital formation (current US$). Why: Current US$ if nominal values are wanted.
  - Unit: current US$ (current)
  - Definition: Gross fixed capital formation includes acquisitions less disposals of fixed assets during the accounting period, including certain specified expenditures on services that add to the value of non-produced assets. This indicator is expressed in current prices, meaning no adjustment has been made to account for price changes over time. This indicator is expressed in United States dollars.

## labsh

Share of labour compensation in GDP at current national prices

Chosen:
- **pwt labsh** (preferred): Share of labour compensation in GDP at current national prices
  - Unit: share (current)
  - Definition: Share of labour compensation in GDP at current national prices

Alternatives to consider:
- **wdi GC.XPN.COMP.CN**: Compensation of employees (current LCU). Why: Government compensation of employees only; not an economy-wide labour share.
  - Unit: current LCU (current)
  - Definition: Compensation of employees is defined as the total remuneration, in cash or in kind, payable by an enterprise to an employee in return for work done by the latter during the accounting period. This indicator is expressed in current prices, meaning no adjustment has been made to account for price changes over time. This series is expressed in local currency units.

## Questions

- Sectoral employment counts are not available in WDI or PWT; should the pipeline multiply the WDI sectoral employment shares by PWT persons engaged (a transformation to add to the spec), or wait for ILOSTAT counts once it is searchable?
- The WDI sectoral employment shares are modeled ILO estimates, and for Uzbekistan these may differ from national statistics; is that acceptable?
- Should real value added and GFCF be in constant US$ (cross-country comparable) or in constant local currency (closer to national accounts)?
- Gross fixed capital formation was taken in real terms to match real value added; do you prefer it as percent of GDP or in current prices instead?
- PWT labsh is economy-wide only and for Central Asian countries is often imputed rather than taken from national accounts; is that acceptable, or do you need sectoral labour shares from national sources?

## Catalog searches made

- wdi: value added constant
- wdi: employment in agriculture industry services
- wdi: gross fixed capital formation
- pwt: labour share
- pwt: persons engaged
- wdi: labor share compensation of employees
- wdi: employment total number
