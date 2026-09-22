# Amazon Search by Image API

## Request

- Endpoint: `${NEXSCOPE_PROXY_BASE}/api/v1/tools/research/amazon/searchByImage`
- Method: `POST`
- Content type: `application/json`
- Authentication: `Authorization: Bearer ${NEXSCOPE_API_KEY}`

Managed installations provide credentials automatically. Environment-mode developers must set `NEXSCOPE_AUTH_MODE=environment`, `NEXSCOPE_PROXY_BASE`, and `NEXSCOPE_API_KEY`.

### JSON body

| Parameter | Type | Required | Description |
|---|---|---:|---|
| `imageUrl` | string | Yes | Valid image URL, up to 1,000 characters. |
| `amazonDomain` | string | Yes | Amazon marketplace domain. Supported values: `amazon.com`, `amazon.co.uk`, `amazon.de`, `amazon.fr`, `amazon.it`, `amazon.es`, `amazon.co.jp`, and `amazon.in`. Defaults to `amazon.com`. |
| `sort` | string | No | `default`, `price-asc-rank`, `price-desc-rank`, `rating-asc-rank`, `rating-desc-rank`, `ratings-asc-rank`, or `ratings-desc-rank`. |
| `deliveryZip` | string | No | Marketplace delivery ZIP code or city, up to 1,000 characters. Defaults: US `10001`, UK `EC1A 1BB`, Germany `10115`, France `75001`, Italy `00100`, Spain `28001`, Japan `100-0001`, and India `110034`. |
| `countryOrAreaCode` | string | No | Cross-border destination code such as `CN`, `JP`, `KR`, `TW`, `HK`, `MO`, `SG`, `TH`, `VN`, `PH`, or `MY`. Do not combine it with `deliveryZip`. India does not support cross-border destinations. |
| `aggregateByKeepaData` | boolean | No | Include Keepa-derived sales rank, sales, fees, dimensions, and related fields. |

## Response

| Field | Type | Description |
|---|---|---|
| `total` | integer | Number of rows. |
| `totalCount` | integer | Total result count. |
| `perPage` | integer | Results per page. |
| `currentPage` | integer | Current page number. |
| `type` | string | Render type. |
| `sourceType` | string | Source type. |
| `columns` | array | Render columns. |
| `costToken` | integer | Tokens consumed by the request. |
| `products` | array | Product results. |

### Product fields

| Field | Type | Description |
|---|---|---|
| `asin` | string | Amazon Standard Identification Number. |
| `title` | string | Product title. |
| `imageUrl` | string | Product image URL. |
| `asinUrl` | string | Amazon product-detail URL. |
| `price` | number | Current price in the currency identified by `currency`. |
| `oldPrice` | number | Previous or strikethrough price. |
| `currency` | string | Currency code. |
| `rating` | number | Current rating from 0.0 to 5.0. |
| `ratings` | integer | Rating count. |
| `brand` | string | Brand. |
| `sourceTool` | string | Source tool. |
| `sourceType` | string | Source type. |

When `aggregateByKeepaData` is `true`, products can also include:

| Field | Type | Description |
|---|---|---|
| `salesRank`, `salesRank30`, `salesRank90`, `salesRank180` | integer | Current and average sales ranks. |
| `monthlySalesUnits`, `monthlySalesRevenue` | number | Monthly sales units and revenue. |
| `monthlySalesUnits1MonthAgo` ... `monthlySalesUnits12MonthsAgo` | integer | Historical monthly sales units. |
| `reviewCount` | integer | Review count. |
| `fbaFees` | number | FBA fee. |
| `profit`, `referralFeePercentage` | number | Profit and referral-fee percentages. |
| `fulfillment` | string | `AMZ`, `FBA`, or `FBM`. |
| `primePrice` | number | Prime price. |
| `buyBoxSellerId`, `parentAsin`, `manufacturer`, `model`, `color`, `material` | string | Product and seller attributes. |
| `sellerNum`, `variationNum` | integer | Seller and variation counts. |
| `availableDate`, `lastUpdate` | string | Availability and update timestamps in `yyyy-MM-dd HH:mm:ss` format. |
| `weight`, `dimension`, `packageWeight`, `packageDimensions` | string | Weight and dimension values. |
| `itemLength`, `itemWidth`, `itemHeight` | integer | Item dimensions in millimeters; unavailable values can be `0` or `-1`. |
| `packageLength`, `packageWidth`, `packageHeight` | integer | Package dimensions in millimeters. |
| `packageQuantity` | integer | Package quantity; unavailable values can be `0` or `-1`. |
| `dimensionsType`, `categoryTree`, `urlSlug` | string | Dimension, category, and URL metadata. |
| `categoryTreeId` | string | Category-tree ID. |
| `rootCategory` | integer | Root category ID. |
| `isAdultProduct`, `isHazmat` | boolean | Product safety flags. |
| `productImageUrls` | array | Product image URLs. |

## Errors

The API normally returns HTTP `200` and reports business status in `errorCode`. Authentication failures return HTTP `401` with `errorCode` `401`.

| Code | Meaning | Action |
|---:|---|---|
| `200` | Success | Parse the response fields. |
| `401` | Authentication failed | Reconnect the managed installation or verify environment-mode credentials. |
| `402` | Insufficient credits | Add credits to the NexScope account. |
| Other | Business error | Read `errmsg` and report the failure without inventing results. |

```json
{
  "errcode": 401,
  "errmsg": "authorized error"
}
```

## Examples

```bash
curl -X POST "${NEXSCOPE_PROXY_BASE}/api/v1/tools/research/amazon/searchByImage" \
  -H "Authorization: Bearer ${NEXSCOPE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "imageUrl": "https://m.media-amazon.com/images/I/61pAlIX8SZL._AC_SY575_.jpg",
    "amazonDomain": "amazon.com",
    "sort": "default"
  }'
```

```bash
curl -X POST "${NEXSCOPE_PROXY_BASE}/api/v1/tools/research/amazon/searchByImage" \
  -H "Authorization: Bearer ${NEXSCOPE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "imageUrl": "https://m.media-amazon.com/images/I/61pAlIX8SZL._AC_SY575_.jpg",
    "amazonDomain": "amazon.com",
    "sort": "price-asc-rank",
    "aggregateByKeepaData": true
  }'
```

```bash
curl -X POST "${NEXSCOPE_PROXY_BASE}/api/v1/tools/research/amazon/searchByImage" \
  -H "Authorization: Bearer ${NEXSCOPE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "imageUrl": "https://m.media-amazon.com/images/I/61pAlIX8SZL._AC_SY575_.jpg",
    "amazonDomain": "amazon.co.jp",
    "countryOrAreaCode": "CN"
  }'
```

## Feedback API

This endpoint is separate from the tool API. Do not mix the base URLs.

- Method: `POST`
- Endpoint: `https://skill-api.nexscope.com/api/v1/public/feedback`
- Content type: `application/json`

```json
{
  "skillName": "nexscope-xxx-xxx",
  "sentiment": "POSITIVE",
  "category": "OTHER",
  "content": "Results were accurate, user was satisfied."
}
```

- `skillName`: use the skill `name` from YAML frontmatter.
- `sentiment`: `POSITIVE`, `NEUTRAL`, or `NEGATIVE`.
- `category`: `BUG`, `COMPLAINT`, `SUGGESTION`, or `OTHER`.
- `content`: explain the user intent, observed result, and reason for the feedback.
