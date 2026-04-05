--
-- PostgreSQL database dump
--

\restrict 8fB2JxoaBTBdY10fhAGfHTCra083SBsJbsfDrKolgxRpmK1Yirh7UDppyPjapsF

-- Dumped from database version 18.3
-- Dumped by pg_dump version 18.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: author; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.author (
    id integer NOT NULL,
    first_name text,
    last_name text,
    full_name text
);


--
-- Name: author_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.author_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: author_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.author_id_seq OWNED BY public.author.id;


--
-- Name: publication; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.publication (
    id integer NOT NULL,
    repository_id integer,
    title text,
    abstract text,
    source_url text,
    embedding public.vector(1024),
    date timestamp without time zone,
    oai_identifier text
);


--
-- Name: publication_author; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.publication_author (
    publication_id integer NOT NULL,
    author_id integer NOT NULL
);


--
-- Name: publication_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.publication_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: publication_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.publication_id_seq OWNED BY public.publication.id;


--
-- Name: repository; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.repository (
    id integer NOT NULL,
    name text NOT NULL,
    oai_endpoint text NOT NULL,
    last_harvest timestamp without time zone,
    refresh_interval integer
);


--
-- Name: repository_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.repository_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: repository_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.repository_id_seq OWNED BY public.repository.id;


--
-- Name: author id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.author ALTER COLUMN id SET DEFAULT nextval('public.author_id_seq'::regclass);


--
-- Name: publication id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publication ALTER COLUMN id SET DEFAULT nextval('public.publication_id_seq'::regclass);


--
-- Name: repository id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repository ALTER COLUMN id SET DEFAULT nextval('public.repository_id_seq'::regclass);


--
-- Name: author author_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.author
    ADD CONSTRAINT author_pkey PRIMARY KEY (id);


--
-- Name: publication_author publication_author_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publication_author
    ADD CONSTRAINT publication_author_pkey PRIMARY KEY (publication_id, author_id);


--
-- Name: publication publication_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publication
    ADD CONSTRAINT publication_pkey PRIMARY KEY (id);


--
-- Name: repository repository_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repository
    ADD CONSTRAINT repository_pkey PRIMARY KEY (id);


--
-- Name: author unique_author_full_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.author
    ADD CONSTRAINT unique_author_full_name UNIQUE (full_name);


--
-- Name: idx_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embedding ON public.publication USING ivfflat (embedding public.vector_cosine_ops);


--
-- Name: idx_publication_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_publication_date ON public.publication USING btree (date);


--
-- Name: idx_publication_repository; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_publication_repository ON public.publication USING btree (repository_id);


--
-- Name: uq_publication_oai_identifier; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_publication_oai_identifier ON public.publication USING btree (oai_identifier);


--
-- Name: publication_author publication_author_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publication_author
    ADD CONSTRAINT publication_author_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.author(id);


--
-- Name: publication_author publication_author_publication_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publication_author
    ADD CONSTRAINT publication_author_publication_id_fkey FOREIGN KEY (publication_id) REFERENCES public.publication(id);


--
-- Name: publication publication_repository_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publication
    ADD CONSTRAINT publication_repository_id_fkey FOREIGN KEY (repository_id) REFERENCES public.repository(id);


--
-- PostgreSQL database dump complete
--

\unrestrict 8fB2JxoaBTBdY10fhAGfHTCra083SBsJbsfDrKolgxRpmK1Yirh7UDppyPjapsF

