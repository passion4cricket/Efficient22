-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- Host: localhost
-- Generation Time: May 29, 2026 at 12:50 PM
-- Server version: 10.4.28-MariaDB
-- PHP Version: 8.2.4

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `efficient22`
--

-- --------------------------------------------------------

--
-- Table structure for table `product_comparison`
--

CREATE TABLE `product_comparison` (
  `id` int(11) NOT NULL,
  `product_name` varchar(255) DEFAULT NULL,
  `brand` varchar(255) DEFAULT NULL,
  `store` varchar(255) DEFAULT NULL,
  `city` varchar(255) DEFAULT NULL,
  `shopify_quantity` int(11) DEFAULT NULL,
  `zoho_quantity` decimal(12,2) DEFAULT NULL,
  `quantity_difference` decimal(12,2) DEFAULT NULL,
  `status` varchar(50) DEFAULT NULL,
  `last_updated` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `product_details`
--

CREATE TABLE `product_details` (
  `id` bigint(20) NOT NULL,
  `marketplace` enum('amazon','flipkart','shopify') NOT NULL,
  `marketplace_product_id` varchar(255) DEFAULT NULL,
  `sku` varchar(255) DEFAULT NULL,
  `parent_sku` varchar(255) DEFAULT NULL,
  `group_id` varchar(255) DEFAULT NULL,
  `title` varchar(500) DEFAULT NULL,
  `product_name` varchar(500) DEFAULT NULL,
  `description` longtext DEFAULT NULL,
  `brand` varchar(255) DEFAULT NULL,
  `manufacturer` varchar(255) DEFAULT NULL,
  `vendor` varchar(255) DEFAULT NULL,
  `product_type` varchar(255) DEFAULT NULL,
  `product_category` varchar(255) DEFAULT NULL,
  `category` varchar(255) DEFAULT NULL,
  `tags` text DEFAULT NULL,
  `mrp` decimal(12,2) DEFAULT NULL,
  `selling_price` decimal(12,2) DEFAULT NULL,
  `sale_price` decimal(12,2) DEFAULT NULL,
  `compare_at_price` decimal(12,2) DEFAULT NULL,
  `cost_price` decimal(12,2) DEFAULT NULL,
  `currency` varchar(20) DEFAULT 'INR',
  `stock` int(11) DEFAULT 0,
  `inventory_qty` int(11) DEFAULT 0,
  `inventory_policy` varchar(100) DEFAULT NULL,
  `inventory_tracker` varchar(255) DEFAULT NULL,
  `weight` decimal(12,3) DEFAULT NULL,
  `weight_unit` varchar(50) DEFAULT NULL,
  `length` decimal(12,3) DEFAULT NULL,
  `breadth` decimal(12,3) DEFAULT NULL,
  `height` decimal(12,3) DEFAULT NULL,
  `dimension_unit` varchar(50) DEFAULT NULL,
  `color` varchar(255) DEFAULT NULL,
  `size` varchar(255) DEFAULT NULL,
  `material` varchar(255) DEFAULT NULL,
  `pattern_name` varchar(255) DEFAULT NULL,
  `style_name` varchar(255) DEFAULT NULL,
  `model_name` varchar(255) DEFAULT NULL,
  `model_number` varchar(255) DEFAULT NULL,
  `part_number` varchar(255) DEFAULT NULL,
  `sport_type` varchar(255) DEFAULT NULL,
  `department_name` varchar(255) DEFAULT NULL,
  `target_gender` varchar(100) DEFAULT NULL,
  `age_group` varchar(100) DEFAULT NULL,
  `ideal_for` varchar(255) DEFAULT NULL,
  `main_image_url` text DEFAULT NULL,
  `other_image_url_1` text DEFAULT NULL,
  `other_image_url_2` text DEFAULT NULL,
  `other_image_url_3` text DEFAULT NULL,
  `other_image_url_4` text DEFAULT NULL,
  `other_image_url_5` text DEFAULT NULL,
  `swatch_image_url` text DEFAULT NULL,
  `video_url` text DEFAULT NULL,
  `seo_title` varchar(500) DEFAULT NULL,
  `seo_description` text DEFAULT NULL,
  `search_keywords` text DEFAULT NULL,
  `domestic_warranty` varchar(255) DEFAULT NULL,
  `international_warranty` varchar(255) DEFAULT NULL,
  `warranty_summary` text DEFAULT NULL,
  `warranty_service_type` text DEFAULT NULL,
  `fulfillment_type` varchar(255) DEFAULT NULL,
  `shipping_provider` varchar(255) DEFAULT NULL,
  `handling_time` int(11) DEFAULT NULL,
  `shipping_template` varchar(255) DEFAULT NULL,
  `hsn` varchar(100) DEFAULT NULL,
  `tax_code` varchar(100) DEFAULT NULL,
  `product_tax_code` varchar(100) DEFAULT NULL,
  `country_of_origin` varchar(255) DEFAULT NULL,
  `barcode` varchar(255) DEFAULT NULL,
  `ean_upc` varchar(255) DEFAULT NULL,
  `product_id` varchar(255) DEFAULT NULL,
  `product_id_type` varchar(255) DEFAULT NULL,
  `listing_status` varchar(255) DEFAULT NULL,
  `published` tinyint(1) DEFAULT 0,
  `item_condition` varchar(255) DEFAULT NULL,
  `option1_name` varchar(255) DEFAULT NULL,
  `option1_value` varchar(255) DEFAULT NULL,
  `option2_name` varchar(255) DEFAULT NULL,
  `option2_value` varchar(255) DEFAULT NULL,
  `option3_name` varchar(255) DEFAULT NULL,
  `option3_value` varchar(255) DEFAULT NULL,
  `qc_status` varchar(255) DEFAULT NULL,
  `qc_failed_reason` text DEFAULT NULL,
  `browse_nodes` text DEFAULT NULL,
  `bullet_points` text DEFAULT NULL,
  `extra_attributes` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`extra_attributes`)),
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `shopify_products`
--

CREATE TABLE `shopify_products` (
  `id` int(11) NOT NULL,
  `product_name` varchar(255) DEFAULT NULL,
  `brand` varchar(255) DEFAULT NULL,
  `variant` varchar(255) DEFAULT NULL,
  `sku` varchar(255) DEFAULT NULL,
  `store` varchar(255) DEFAULT NULL,
  `city` varchar(255) DEFAULT NULL,
  `quantity` int(11) DEFAULT NULL,
  `total_variant_quantity` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `shopify_product_options`
--

CREATE TABLE `shopify_product_options` (
  `id` int(11) NOT NULL,
  `product_id` int(11) DEFAULT NULL,
  `option_name` varchar(100) DEFAULT NULL,
  `option_value` varchar(100) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `zoho_items`
--

CREATE TABLE `zoho_items` (
  `id` int(11) NOT NULL,
  `item_id` varchar(50) NOT NULL,
  `name` varchar(255) DEFAULT NULL,
  `brand` varchar(255) DEFAULT NULL,
  `status` varchar(50) DEFAULT NULL,
  `rate` decimal(12,2) DEFAULT NULL,
  `purchase_rate` decimal(12,2) DEFAULT NULL,
  `stock_on_hand` decimal(12,2) DEFAULT NULL,
  `available_stock` decimal(12,2) DEFAULT NULL,
  `sku` varchar(255) DEFAULT NULL,
  `vendor_name` varchar(255) DEFAULT NULL,
  `hsn_or_sac` varchar(50) DEFAULT NULL,
  `created_time` datetime DEFAULT NULL,
  `last_modified_time` datetime DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `zoho_item_dimensions`
--

CREATE TABLE `zoho_item_dimensions` (
  `id` int(11) NOT NULL,
  `item_id` varchar(50) DEFAULT NULL,
  `length` decimal(10,2) DEFAULT NULL,
  `width` decimal(10,2) DEFAULT NULL,
  `height` decimal(10,2) DEFAULT NULL,
  `weight` decimal(10,2) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `zoho_item_taxes`
--

CREATE TABLE `zoho_item_taxes` (
  `id` int(11) NOT NULL,
  `item_id` varchar(50) DEFAULT NULL,
  `tax_name` varchar(100) DEFAULT NULL,
  `tax_percentage` decimal(5,2) DEFAULT NULL,
  `tax_specification` varchar(50) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Indexes for dumped tables
--

--
-- Indexes for table `product_comparison`
--
ALTER TABLE `product_comparison`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `unique_compare` (`product_name`,`brand`,`store`,`city`) USING HASH;

--
-- Indexes for table `product_details`
--
ALTER TABLE `product_details`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `shopify_products`
--
ALTER TABLE `shopify_products`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `unique_product` (`product_name`,`variant`,`store`);

--
-- Indexes for table `shopify_product_options`
--
ALTER TABLE `shopify_product_options`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `unique_option` (`product_id`,`option_name`);

--
-- Indexes for table `zoho_items`
--
ALTER TABLE `zoho_items`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `item_id` (`item_id`),
  ADD KEY `idx_item_id` (`item_id`);

--
-- Indexes for table `zoho_item_dimensions`
--
ALTER TABLE `zoho_item_dimensions`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `unique_dimension` (`item_id`);

--
-- Indexes for table `zoho_item_taxes`
--
ALTER TABLE `zoho_item_taxes`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `unique_tax` (`item_id`,`tax_name`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `product_comparison`
--
ALTER TABLE `product_comparison`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `product_details`
--
ALTER TABLE `product_details`
  MODIFY `id` bigint(20) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `shopify_products`
--
ALTER TABLE `shopify_products`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `shopify_product_options`
--
ALTER TABLE `shopify_product_options`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `zoho_items`
--
ALTER TABLE `zoho_items`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `zoho_item_dimensions`
--
ALTER TABLE `zoho_item_dimensions`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `zoho_item_taxes`
--
ALTER TABLE `zoho_item_taxes`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- Constraints for dumped tables
--

--
-- Constraints for table `shopify_product_options`
--
ALTER TABLE `shopify_product_options`
  ADD CONSTRAINT `shopify_product_options_ibfk_1` FOREIGN KEY (`product_id`) REFERENCES `shopify_products` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `zoho_item_dimensions`
--
ALTER TABLE `zoho_item_dimensions`
  ADD CONSTRAINT `zoho_item_dimensions_ibfk_1` FOREIGN KEY (`item_id`) REFERENCES `zoho_items` (`item_id`) ON DELETE CASCADE;

--
-- Constraints for table `zoho_item_taxes`
--
ALTER TABLE `zoho_item_taxes`
  ADD CONSTRAINT `zoho_item_taxes_ibfk_1` FOREIGN KEY (`item_id`) REFERENCES `zoho_items` (`item_id`) ON DELETE CASCADE;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
