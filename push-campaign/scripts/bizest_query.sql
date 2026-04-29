-- 비제스트 RAW 조회 쿼리
-- 파라미터: {send_dt} → YYYY-MM-DD 형태로 치환 후 실행
-- 파티션 키: DATE(release_start_date_time) — WHERE 절에 반드시 포함

WITH base_data AS (
    SELECT
        id,
        requested_start_date_time,
        release_start_date_time,
        MAX(ad.sourceBrandId)                                                  AS sourceBrandId,
        event_name,
        main_title,
        MAX(CASE WHEN attr.label = '프로모션 내용' THEN attr.value END)          AS promotion_content,
        landing_url,
        CASE
            WHEN landing_url LIKE '%/campaign/%' THEN 'campaign'
            WHEN landing_url LIKE '%/content/%'  THEN 'content'
            ELSE NULL
        END AS content_type,
        CASE
            WHEN landing_url LIKE '%/campaign/%' THEN regexp_extract(landing_url, 'campaign/([^/]+)', 1)
            WHEN landing_url LIKE '%/content/%'  THEN regexp_extract(landing_url, 'content/([^/]+)',  1)
            ELSE NULL
        END AS content_id,
        remarks,
        register_team_name,
        register_id,
        request_status
    FROM (
        SELECT *,
               from_json(additional_attributes,
                         'ARRAY<STRUCT<attributes: ARRAY<STRUCT<label: STRING, value: STRING>>>>') AS data,
               from_json(ad_accounts,
                         'ARRAY<STRUCT<sourceBrandId: STRING>>')                                   AS ad_data
        FROM ocmp.marketing_slot.application
        WHERE marketing_inventory_id IN (2, 26, 89, 90, 91, 58)
          AND DATE(release_start_date_time) = '{send_dt}'
    )
    LATERAL VIEW OUTER explode(data)              AS groups
    LATERAL VIEW OUTER explode(groups.attributes) AS attr
    LATERAL VIEW OUTER explode(ad_data)           AS ad
    GROUP BY
        id, requested_start_date_time, release_start_date_time,
        event_name, main_title, landing_url,
        remarks, register_team_name, register_id, request_status
),
campaign_thumb AS (
    SELECT b.html_code, a.thumbnail_img
    FROM `musinsa-rt`.campaign.campaign         a
    JOIN `musinsa-rt`.campaign.campaign_mapping b ON a.uid = b.uid
)

SELECT
    b.id,
    b.requested_start_date_time,
    b.release_start_date_time,
    b.sourceBrandId,
    b.event_name,
    b.main_title,
    b.promotion_content,
    b.landing_url,
    CASE
        WHEN b.content_type = 'content'  THEN c.thumbnail_url
        WHEN b.content_type = 'campaign' THEN m.thumbnail_img
        ELSE NULL
    END                AS img_url,
    b.remarks,
    b.register_team_name,
    b.register_id,
    b.request_status
FROM base_data b
LEFT JOIN musinsa.contents.musinsa_content_meta c
       ON b.content_type = 'content'  AND b.content_id = CAST(c.cms_id AS STRING)
LEFT JOIN campaign_thumb m
       ON b.content_type = 'campaign' AND b.content_id = m.html_code
ORDER BY b.requested_start_date_time
